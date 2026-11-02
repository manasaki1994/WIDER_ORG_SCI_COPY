"""`wider report prisma ...`: the PRISMA reporting assistant (prisma-reporting-assistant D13), the CLI twin of the PRISMA
reporting page. Every command calls the engine in :mod:`wider.recall.reporting` the routes call. Attached in cli.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from .core.project import Project
from .recall import reporting as r

prisma_app = typer.Typer(help="PRISMA 2020 and PRISMA-S reporting: what each item asks, what WIDER recorded, your text, "
                              "the flow counts and the exports.", no_args_is_help=True)

DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")
CHECKLIST = typer.Option("prisma-2020", "--checklist", "-c", help="prisma-2020 or prisma-s.")
PROTOCOL = typer.Option(None, "--protocol", help="Screening protocol (default: the latest protocol with screening decisions).")
SET = typer.Option(None, "--set", min=1, help="Result set of the protocol (default: the active set).")
BY = typer.Option(None, "--by", help="Who makes the change (default: WIDER_REVIEWER or the login name).")
JSON = typer.Option(False, "--json", help="Print JSON, the same as the page's route.")
ITEM = typer.Argument(..., help="Item number as the checklist numbers it (e.g. 7, 16a).")


def _run(directory: Path, fn):
    p = Project.find(directory)
    try:
        return fn(p)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))}[/red]")
        raise typer.Exit(1) from e
    finally:
        p.close()


def _changed(res: dict[str, Any], done: str) -> None:
    if res["changed"]:
        rprint(f"{escape(done)} (change {res['change_id']}; undo: wider history undo {res['change_id']})")
    else:
        rprint("nothing changed")


def _print_json(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


@prisma_app.command("status")
def status(checklist: str = CHECKLIST, protocol: str = PROTOCOL, result_set: int = SET,
           only: str = typer.Option(None, "--status", help="Only items with this status, e.g. \"Researcher input needed\"."),
           to_check: bool = typer.Option(False, "--to-check", help="Only items whose text is outdated, unreadable or "
                                                                  "still has gaps in brackets."),
           as_json: bool = JSON, directory: Path = DIR):
    """List the items of one checklist in PRISMA order: item, topic, status and "Your text"."""
    cl = _run(directory, lambda p: r.checklist_key(checklist))
    rows = _run(directory, lambda p: r.status_rows(p, cl, protocol, result_set, to_check=to_check, only=only))
    if as_json:
        _print_json(rows)
        return
    rprint(f"{r.TITLES[cl]}: {len(rows)} item{'s' if len(rows) != 1 else ''}")
    t = Table("item", "topic", "status", "your text")
    section = None
    for x in rows:
        if x["section"] != section:
            section = x["section"]
            t.add_row(f"[bold]{escape(section)}[/bold]", "", "", "")
        mine = x["your_text_label"]
        if x["your_text"] in ("outdated", "unreadable"):
            mine = f"[red]{escape(mine)}[/red]"
        else:
            mine = escape(mine) + (f"; {x['gaps_left']} gap{'s' if x['gaps_left'] != 1 else ''} in brackets"
                                   if x["gaps_left"] else "")
        t.add_row(x["no"], escape(x["topic"]), escape(x["status"]), mine)
    rprint(t)


def _print_lines(lines: list[dict[str, Any]]) -> None:
    for ln in lines:
        rprint(f"  - {escape(ln['text'])} [dim]({escape(ln['source'])})[/dim]")


def _print_item(v: dict[str, Any], d: dict[str, Any] | None) -> None:
    title = r.TITLES[v["checklist"]]
    rprint(f"[bold]{title} item {v['no']}: {escape(v['topic'])}[/bold] ({escape(v['section'])})")
    rprint(f"Status: {escape(v['status'])}" + (f" (WIDER: {escape(v['wider_status'])})" if v["status"] != v["wider_status"] else ""))
    if v.get("has_evidence"):
        rprint(escape(v["has_evidence"]))
    rprint("\n[bold]PRISMA asks[/bold]")
    rprint(f"  {escape(v['official'])} ({escape(v['source'])})")
    for e in v["elements"]:
        rprint(f"  - {escape(e['text'])}")
    if v["outside_sentence"]:
        rprint(escape(v["outside_sentence"]))
    rprint("\n[bold]WIDER recorded[/bold]")
    if v["evidence"]:
        _print_lines(v["evidence"])
    else:
        rprint("  nothing")
    rprint("\n[bold]Still missing[/bold]")
    if v["missing"]:
        for m in v["missing"]:
            mine = f"; {r.COVERED_BY_YOU}" if m.get("covered_by_you") else ""
            rprint(f"  - {escape(m['text'])}: {escape(m['say'])}{escape(mine)}")
    else:
        rprint("  nothing")
    if v["related"]:
        rprint(f"\n[bold]{escape(v['related_heading'])}[/bold]")
        for x in v["related"]:
            rprint(f"  - {r.TITLES[x['checklist']]} item {x['no']}: {escape(x['topic'])} ({escape(x['status'])})"
                   + (f"; text: {escape(x['first_line'])}" if x["first_line"] else ""))
    y = v["yours"]
    rprint("\n[bold]Your text[/bold]")
    if y.get("unreadable"):
        rprint(f"  [red]{escape(y['unreadable'])}[/red]")
    elif y["not_applicable"]:
        rprint(f"  Not applicable: {escape(y['not_applicable']['reason'])}")
    if y["text"].strip():
        for t in y["text"].splitlines():
            rprint(f"  {escape(t)}")
        rprint(f"  ({escape(y['your_text_label'])}"
               + (f"; saved for {escape(y['saved_for'])}" if y["saved_for"] else "") + ")")
        if y["gaps_left"]:
            rprint(f"  Your text still has {y['gaps_left']} gap{'s' if y['gaps_left'] != 1 else ''} in brackets")
    elif not y.get("unreadable"):
        rprint("  none")
    if y["where"]:
        rprint(f"  Where reported: {escape(y['where'])}")
    out = y.get("outdated")
    if out:
        rprint("[red]Your text is outdated: WIDER's evidence for this item changed since you saved it.[/red]"
               if not out["reason"] else f"[red]{escape(out['reason'])}[/red]")
        rprint("  saved with:")
        _print_lines(out["saved"])
        rprint("  now:")
        _print_lines(out["current"])
        for line in out["inconsistencies"]:
            rprint(f"[red]{escape(line)}[/red]")
    if d:
        rprint("\n[bold]Draft from WIDER's records[/bold] (not your text until you save it: edit --from-draft)")
        rprint(f"  {escape(d['text'])}")


@prisma_app.command("show")
def show(no: str = ITEM, checklist: str = CHECKLIST, protocol: str = PROTOCOL, result_set: int = SET,
         as_json: bool = JSON, directory: Path = DIR):
    """One item: what PRISMA asks, what WIDER recorded, what is still missing, the related items, your text and the
    draft. Viewing records nothing."""
    def go(p):
        ctx = r.context(p, protocol, result_set)
        v = r.show(p, checklist, no, ctx)
        return v, r.draft(p, checklist, no, ctx)

    v, d = _run(directory, go)
    if as_json:
        _print_json({**v, "draft": d})
        return
    _print_item(v, d)


@prisma_app.command("edit")
def edit(no: str = ITEM, checklist: str = CHECKLIST,
         text: str = typer.Option(None, "--text", help="Your text for the item."),
         file: Path = typer.Option(None, "--file", exists=True, dir_okay=False, help="Read your text from a file."),
         from_draft: bool = typer.Option(False, "--from-draft", help="Save WIDER's current draft as your text."),
         from_item: str = typer.Option(None, "--from-item", help="Save a related item's text as this item's, e.g. "
                                                                 "prisma-s:13."),
         covers: str = typer.Option(None, "--covers", help="Comma-separated elements your text covers (empty: none)."),
         where: str = typer.Option(None, "--where", help="Where the item is reported, e.g. \"p. 6\"."),
         keep: bool = typer.Option(False, "--keep", help="Keep your text for the current evidence (clears \"outdated\")."),
         protocol: str = PROTOCOL, result_set: int = SET, by: str = BY, directory: Path = DIR):
    """Save your text for one item, kept apart from WIDER's records. Listed in History; undo with
    `wider history undo ID`."""
    sources = [x for x in (text is not None, file is not None, from_draft, bool(from_item)) if x]
    if len(sources) > 1:
        rprint("[red]give one of --text, --file, --from-draft and --from-item[/red]")
        raise typer.Exit(1)
    if not sources and where is None and covers is None and not keep:
        rprint("[red]give --text, --file, --from-draft, --from-item, --covers, --where or --keep[/red]")
        raise typer.Exit(1)
    if file is not None:
        text = file.read_text(encoding="utf-8")
    cov = None if covers is None else [c for c in covers.split(",") if c.strip()]
    res = _run(directory, lambda p: r.save_text(p, checklist, no, text=text, covers=cov, where=where,
                                                 from_draft=from_draft, from_item=from_item, keep=keep,
                                                 protocol=protocol, result_set=result_set, by=by))
    _changed(res, f"{r.item_label(r.checklist_key(checklist), no)}: saved")


@prisma_app.command("na")
def na(no: str = ITEM, checklist: str = CHECKLIST,
       reason: str = typer.Option(None, "--reason", help="Why the item does not apply (required to mark it)."),
       remove: bool = typer.Option(False, "--clear", help="Remove the not-applicable mark."),
       by: str = BY, directory: Path = DIR):
    """Mark an item not applicable with a reason, or remove the mark. Undo with `wider history undo ID`."""
    res = _run(directory, lambda p: r.mark_not_applicable(p, checklist, no, reason=reason, remove=remove, by=by))
    _changed(res, f"{r.item_label(r.checklist_key(checklist), no)}: "
                  + ("not-applicable mark removed" if remove else "marked not applicable"))


@prisma_app.command("clear")
def clear(no: str = ITEM, checklist: str = CHECKLIST, by: str = BY, directory: Path = DIR):
    """Clear your text, covers and location for one item; earlier versions stay. Undo with `wider history undo ID`."""
    res = _run(directory, lambda p: r.clear(p, checklist, no, by=by))
    _changed(res, f"{r.item_label(r.checklist_key(checklist), no)}: text cleared")


def _parse_counts(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            rprint(f"[red]--count takes NAME=N, got {escape(v)!r}[/red]")
            raise typer.Exit(1)
        name, n = v.split("=", 1)
        out[name.strip()] = n.strip()
    return out


def _parse_excluded(values: list[str] | None) -> list[tuple[str, str]] | None:
    if not values:
        return None
    if [v.strip().lower() for v in values] == ["none"]:
        return []
    rows = []
    for v in values:
        reason, _, n = v.rpartition("=")
        rows.append((reason, n) if reason else (v, ""))
    return rows


def _was(n: Any) -> Any:
    """A box new since the save (a platform imported later), or gone now, has no count on that side."""
    return "not in the diagram" if n is None else n


def _print_flow(st: dict[str, Any]) -> None:
    if st["message"]:
        rprint(escape(st["message"]))
    else:
        rprint(f"PRISMA 2020 flow, {escape(st['protocol'])} set {st['result_set']}")
    t = Table("box", "count", "source")
    for b in st["boxes"]:
        mark = "†" if b["source"] == "researcher" else ""
        t.add_row(escape(b["label"]), escape(b["text"]) + mark, b["source"])
    rprint(t)
    for x in st["excluded"]:
        rprint(f"  excluded: {escape(x['reason'])} (n = {x['n']:,}†)")
    if any(b["source"] == "researcher" for b in st["boxes"]):
        rprint("† entered by the researcher")
    if st["other_sets"]:
        rprint(escape(st["other_sets"]))
    if st["unreadable"]:
        rprint("[red]could not read your flow counts for this set[/red]")
    for line in st["checks"] + ([st["exports_stale"]] if st.get("exports_stale") else []):
        rprint(f"[red]{escape(line)}[/red]")
    if st["outdated"]:
        for c in st["outdated"]["changed"]:
            rprint(f"  {escape(c['label'])}: {_was(c['saved'])} when you saved, {_was(c['now'])} now")


@prisma_app.command("flow")
def flow(count: list[str] = typer.Option(None, "--count", help="NAME=N for a box WIDER does not record (registers, "
                                                               "automation_removed, not_retrieved, studies, reports); "
                                                               "NAME= removes it. Repeatable."),
         excluded: list[str] = typer.Option(None, "--excluded", help="\"REASON=N\" reports excluded with their reason; "
                                                                     "replaces the list; \"none\" empties it. Repeatable."),
         remove: bool = typer.Option(False, "--clear", help="Remove your counts for this protocol and set."),
         protocol: str = PROTOCOL, result_set: int = SET, by: str = BY, as_json: bool = JSON, directory: Path = DIR):
    """Show the PRISMA 2020 flow boxes with their source, and save the counts WIDER does not record. Saves and
    clears are listed in History; undo with `wider history undo ID`."""
    counts = _parse_counts(count)
    rows = _parse_excluded(excluded)

    def go(p):
        res = None
        if remove:
            res = r.clear_flow(p, protocol, result_set, by=by)
        elif counts or rows is not None:
            res = r.save_flow(p, protocol, result_set, counts=counts, excluded=rows, by=by)
        return res, r.flow_state(p, protocol, result_set)

    res, st = _run(directory, go)
    if as_json:
        _print_json(st)
        return
    if res is not None:
        _changed(res, "flow counts cleared" if remove else "flow counts saved")
    _print_flow(st)


@prisma_app.command("export")
def export(protocol: str = PROTOCOL, result_set: int = SET, directory: Path = DIR):
    """Write exports/prisma_reporting/ (both checklists as Markdown, CSV, JSON and Word, the methods text and its Word
    documentation, the editable flow diagram) and redraw exports/prisma_2020.svg."""
    res = _run(directory, lambda p: r.export(p, protocol, result_set))
    rprint(escape(res["message"]))
    for f in res["files"]:
        rprint(f"  {escape(f)}")
