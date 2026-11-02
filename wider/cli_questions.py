"""`wider questions ...`: questions the tool raised when in doubt, the same ones the Questions page shows."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from .cli_refine import CONFIRM_OPT, COUNT_OPT, KEEP_OPT, revision_exit, seen_exit, seen_guard
from .core import questions as qs
from .core.project import Project

questions_app = typer.Typer(help="Questions raised when the tool is in doubt: list, answer, dismiss, queue or ask.",
                            no_args_is_help=True)
DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")
BY = typer.Option(None, "--by", help="Your name; defaults to $WIDER_REVIEWER, then $USER.")


def _by(by: str | None) -> str:
    """The name as signed: the option's value, else the fallback of :func:`~wider.core.journal.current_author`, which
    records that it came from the environment (an inferred name, researcher-attribution D2)."""
    from .core.journal import current_author

    return current_author(by)


def _run(directory: Path, fn):
    p = Project.find(directory)
    try:
        return fn(p)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@questions_app.command("list")
def list_(all_: bool = typer.Option(False, "--all", help="Also answered and dismissed questions."),
          page: str = typer.Option(None, "--page", help='Only the questions of one page, e.g. "construct" or '
                                   '"setup?task=seeds"; "setup" also takes every setup?task=... page.'),
          directory: Path = DIR):
    """Open questions (or all of them) with their possible answers and what each answer does."""
    rows, mode, hidden = _run(directory, lambda p: (qs.list_questions(p, None if all_ else "open", page=page),
                                                    p.config.doubts, qs.hidden_line(p)))
    rprint(f"mode: {mode} ({'questions wait here' if mode == 'queue' else 'questions open in a dialog in the UI as they arrive'})")
    t = Table("id", "stage", "about", "question", "answers" if not all_ else "status / answer")
    for q in rows:
        opts = " | ".join(o["value"] + (" <value>" if o.get("input") else "") + ("*" if o.get("default") else "")
                          for o in q["options"])
        last = opts if q["status"] == "open" else f"{q['status']}: {(q['answer'] or {}).get('choice', '')} {q.get('applied') or ''}"
        t.add_row(str(q["question_id"]), q["stage"], escape((q["title"] or "")[:50]), escape(q["prompt"][:120]), escape(last))
    rprint(t if rows else "no questions")
    if hidden:
        typer.echo(hidden + ".")
    # each answer's consequence (and a refinement question's evidence), as in the question window (* the default)
    for q in rows:
        if q["status"] != "open" or not any(o.get("consequence") for o in q["options"]):
            continue
        why = ((q["context"] or {}).get("evidence") or {}).get("why")
        typer.echo(f"{q['question_id']} {q['kind']}" + (f" (why: {why})" if why else ""))
        for o in q["options"]:
            typer.echo(f"  {o['value']}{' (default)' if o.get('default') else ''}: {o.get('consequence') or o['label']}")
    waiting = _run(directory, lambda p: qs.counts(p).get("waiting", 0))
    if waiting:
        typer.echo(f"{waiting} waiting for the budget (they open as open questions are answered or dismissed)")


@questions_app.command("answer")
def answer(question_id: int = typer.Argument(...), choice: str = typer.Argument(..., help="One of the question's answers."),
           value: str = typer.Option("", "--value", "-v", help="The DOI, abstract or clarification the answer needs."),
           confirm: bool = CONFIRM_OPT, count: bool = COUNT_OPT, keep: bool = KEEP_OPT, by: str = BY,
           directory: Path = DIR):
    """Answer a question; the answer is applied at once and the change is printed. An answer that saves a synonyms
    version after a freeze or while the uploads are final asks --count-seen or --keep-validation when validation
    papers have been shown."""
    from .construct.artifacts import RevisionNeedsConfirm, SeenChoiceNeeded, save_seen_choice

    p = Project.find(directory)
    seen = seen_guard(p, count, keep)   # only an answer that saves a version asks: the engine says so
    try:
        with save_seen_choice(seen):
            q = qs.answer(p, question_id, choice, value, _by(by), confirm_revision=confirm)
    except RevisionNeedsConfirm as e:   # the answer would save a version after a kept look: nothing recorded
        revision_exit(p, e.notice)
    except SeenChoiceNeeded as e:   # the answer would reopen the query with papers shown: nothing recorded
        seen_exit(p, e.notice)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    rprint(f"question {question_id}: {escape(q['applied'] or 'answered')}")


@questions_app.command("dismiss")
def dismiss(question_id: int = typer.Argument(...), by: str = BY, directory: Path = DIR):
    """Close a question without changing anything."""
    _run(directory, lambda p: qs.dismiss(p, question_id, _by(by)))
    rprint(f"question {question_id} dismissed")


@questions_app.command("mode")
def mode(new: str = typer.Argument(None, help="queue | ask (omit to show the current mode)."), directory: Path = DIR):
    """Show or set whether questions are queued or asked at once in the UI."""
    m = _run(directory, lambda p: qs.set_mode(p, new) if new else p.config.doubts)
    rprint(f"doubts: {m}")


decisions_app = typer.Typer(help="The decision ledger of the refinement interview: every answer, accepted default "
                            "and dismissal, with its evidence and consequence.", no_args_is_help=True)


@decisions_app.command("list")
def decisions_list(round_no: int = typer.Option(None, "--round", help="Only this round (a synonyms version)."),
                   directory: Path = DIR):
    """The decisions, oldest first: round, mode (chosen, default, dismissed), kind, author and what was decided."""
    from .interview import ledger
    for line in _run(directory, lambda p: ledger.lines(ledger.rows(p, round_no))):
        typer.echo(line)


@decisions_app.command("export")
def decisions_export(directory: Path = DIR):
    """Write runs/query/decisions.jsonl and decisions.csv (the replication package copies them)."""
    from .interview import ledger
    r = _run(directory, ledger.export)
    typer.echo(f"{r['decisions']} decisions → {', '.join(r['files'])}")
