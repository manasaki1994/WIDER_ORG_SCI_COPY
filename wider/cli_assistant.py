"""`wider assistant ...`: Ask WIDER from the command line (guide-assistant D19), the same engine as the guide panel's
box. Exit codes: 0 answered (also "WIDER's documents do not explain this."), 1 refused, 2 consent needed without a
terminal and without `--allow-once`, or `forget` without `--yes` and without a terminal. Attached in cli.py.

Answers, questions and section titles are printed as plain text, never as Rich markup: an answer is the model's text
and may hold brackets or markup, which stay literal (D7)."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape

from .assistant.context import GENERAL
from .cli_lookups import _interactive, say_sent
from .core.project import Project

assistant_app = typer.Typer(help="Ask WIDER: questions about WIDER answered from its own documents, never from your "
                                 "records.", no_args_is_help=True)

DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")
BY = typer.Option(None, "--by", help="Your name; defaults to $WIDER_REVIEWER, then $USER. Conversations are kept "
                                     "per name.")
JSON = typer.Option(False, "--json", help="Print JSON.")


def _author(by: str | None) -> str:
    from .core.journal import current_author

    return current_author(by)


def _refuse(p: Project | None, message: str, code: int = 1):
    rprint(f"[red]{escape(message)}[/red]")
    if p is not None:
        p.close()
    raise typer.Exit(code)


def _has_embedding(p: Project) -> bool:
    try:
        p.embedding()
        return True
    except (KeyError, TypeError):
        return False


def _source_lines(based_on: list[dict]) -> list[str]:
    from .assistant.ask import UNREVIEWED_MARK

    out = []
    for b in based_on:
        mark = f" {UNREVIEWED_MARK}" if b.get("unreviewed") else ""
        cmd = f": {b['command']}" if b.get("command") else ""
        out.append(f"  {b['title']}{mark}{cmd}")
    return out


def answer_lines(r: dict) -> list[str]:
    """An answered turn as `wider assistant ask` prints it: the answer, "Based on:", "Show:" and "Draft" lines, the
    notes, "Read:" and "Answered by"."""
    from .guide import guide_text

    out = [r["answer"]]
    if r.get("based_on"):
        out += ["", "Based on:", *_source_lines(r["based_on"])]
    for s in r.get("show") or []:
        out.append(f"Show: {s['button']} ({guide_text(s['page'])['title']})")
    for d in r.get("drafts") or []:
        out += ["", f"Draft ({d['for']}):" if d.get("for") else "Draft:", f"  {d['text']}"]
    if r.get("notes"):
        out += ["", *r["notes"]]
    if r.get("read"):
        out += ["", r["read"]]
    if r.get("answered_by"):
        out.append(r["answered_by"])
    return out


@assistant_app.command("ask")
def ask(question: str = typer.Argument(..., help="Your question about WIDER, its pages or its methods."),
            page: str = typer.Option(GENERAL, "--page", help="The page you ask about, e.g. dedup (its guide is read); "
                                     "leave it out to ask about WIDER in general.", show_default=False),
            depth: str = typer.Option(None, "--depth", help="simpler or detail: answer again in plainer words, or "
                                                            "with the reasons and the auditor's detail."),
            by: str = BY,
            allow_once: bool = typer.Option(False, "--allow-once", help="Contact a backend that needs consent for "
                                                                        "this question only; nothing is stored."),
            as_json: bool = JSON, directory: Path = DIR):
    """Ask WIDER a question. It reads the guide of the page you ask about, WIDER's documents, your project's settings and status, and
    your earlier questions; never a record, a label or a note."""
    from .assistant import ask as A
    from .cli_backend_consent import ensure_backend_consent
    from .core.backend_consent import BackendConsentRequired

    if depth not in (None, "simpler", "detail"):
        _refuse(None, "--depth takes simpler or detail")
    p = Project.find(directory)
    author = _author(by)
    try:
        A._check(p, page, question, author)
        cfg = A.assistant_provider(p)
    except A.Refusal as e:
        _refuse(p, str(e))
    say_sent(p, "assistant", quiet=as_json)
    if _has_embedding(p):
        say_sent(p, "assistant_embed", quiet=as_json)
    consent = ensure_backend_consent(p, cfg, "assistant", once=allow_once)
    once = (consent.get("fingerprint") or True) if consent.get("once") else None
    try:
        r = A.ask(p, page, question, author, depth=depth, allow_once=once)
    except A.Refusal as e:
        _refuse(p, str(e))
    except BackendConsentRequired as e:
        _refuse(p, str(e), 2)
    finally:
        p.close()
    if as_json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return
    print("\n".join(answer_lines(r)))
    if r.get("consent"):
        name = r["consent"]["backend_consent_required"]["backend"]
        print(f"To search by meaning too: wider embed allow {name} --for assistant")
    if r["turn"]:
        print(f"(turn {r['turn']}; if it did not help: wider assistant unhelpful {r['turn']})")


@assistant_app.command("history")
def history_cmd(by: str = BY,
                unanswered: bool = typer.Option(False, "--unanswered", help="Only the questions WIDER's documents "
                                                                            "could not answer or marked Didn't help."),
                as_json: bool = JSON, directory: Path = DIR):
    """Your conversation in this project, numbered from 1."""
    from .assistant import ask as A
    from .assistant import store

    p = Project.find(directory)
    author = _author(by)
    if not author:
        _refuse(p, A.NO_AUTHOR)
    try:
        rows = A.history(p, author)
    finally:
        p.close()
    if unanswered:
        keep = {t["n"] for t in store.unanswered(p, author)}
        rows = [t for t in rows if t["n"] in keep]
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("No questions." if unanswered else "No conversation yet.")
        return
    for t in rows:
        why = (" (Didn't help)" if t.get("unhelpful") else " (not answered)") if unanswered else ""
        where = "about WIDER in general" if t.get("page") == GENERAL else f"on {t.get('page')}"
        print(f"{t['n']}. {str(t.get('at') or '')[:10]} {where}: {t['question']}{why}")
        if unanswered:
            continue
        print(f"   {t['answer']}")
        if t.get("docs_changed"):
            print(f"   {A.DOCS_CHANGED}")
        if t.get("based_on"):
            print("   Based on:")
            print("\n".join(f"  {x}" for x in _source_lines(t["based_on"])))


@assistant_app.command("forget")
def forget_cmd(by: str = BY, yes: bool = typer.Option(False, "--yes", help="Delete without asking."),
               directory: Path = DIR):
    """Delete your conversation in this project (only yours). This cannot be undone."""
    from .assistant import ask as A
    from .assistant import store

    p = Project.find(directory)
    author = _author(by)
    if not author:
        _refuse(p, A.NO_AUTHOR)
    n = len(store.turns(p, author))
    turns = f"{n} question and answer" if n == 1 else f"{n} questions and answers"
    if not yes:
        if not _interactive():
            _refuse(p, "Forgetting deletes your conversation and cannot be undone: add --yes.", 2)
        if not typer.confirm(f"Delete your conversation in this project ({turns})? "
                             "This cannot be undone.", default=False):
            p.close()
            print("Nothing was deleted.")
            return
    store.forget(p, author)
    p.close()
    print(f"Deleted your conversation ({turns}).")


@assistant_app.command("unhelpful")
def unhelpful_cmd(turn: int = typer.Argument(..., help="The turn number (wider assistant history)."),
                  undo: bool = typer.Option(False, "--undo", help="Take the mark off again."),
                  by: str = BY, directory: Path = DIR):
    """Mark an answer "Didn't help"; it is then listed by `wider assistant history --unanswered`."""
    from .assistant import ask as A
    from .assistant import store

    p = Project.find(directory)
    author = _author(by)
    if not author:
        _refuse(p, A.NO_AUTHOR)
    try:
        store.mark_unhelpful(p, author, turn, not undo)
    except KeyError as e:
        _refuse(p, str(e).strip("'\""))
    p.close()
    print(f"Turn {turn}: the mark is off." if undo else f"Turn {turn} marked Didn't help.")


@assistant_app.command("budget")
def budget_cmd(value: int = typer.Argument(None, help="Tokens of context a question may send (500 to 200,000)."),
               default: bool = typer.Option(False, "--default", help="Go back to the default for the model's place."),
               by: str = BY, directory: Path = DIR):
    """Show or set how much text a question may send to the model (its context budget)."""
    from .assistant import ask as A
    from .assistant import context as C

    p = Project.find(directory)
    if value is not None and default:
        _refuse(p, "give a number or --default, not both")
    try:
        cfg = A.assistant_provider(p)
    except A.Refusal:
        cfg = None
    try:
        if value is not None or default:
            C.set_budget(p, None if default else value, _author(by) or None)
    except ValueError as e:
        _refuse(p, str(e))
    own = p.config.assistant.context_tokens
    print(f"Ask WIDER budget: {C.budget(p, cfg):,} tokens"
          + ("" if own else " (the default for the model's place)"))
    if own:
        print(f"The default for the model's place is {C.default_budget(p, cfg):,} tokens "
              "(wider assistant budget --default).")
    p.close()


@assistant_app.command("index")
def index_cmd(directory: Path = DIR):
    """Build the search-by-meaning index of WIDER's documents now (the first question builds it otherwise). Only
    WIDER's own documents are embedded; nothing of the project."""
    from .assistant import retrieve as R
    from .assistant.sections import all_sections, docs_key
    from .cli_backend_consent import ensure_backend_consent

    p = Project.find(directory)
    if not _has_embedding(p):
        _refuse(p, "No embedding model is set: Ask WIDER searches by keywords only. Set one on the Model page.")
    say_sent(p, "assistant_embed")
    cfg, note, consent = R.meaning_backend(p)
    if consent:
        ensure_backend_consent(p, p.embedding(), "assistant", embedding=True)
        cfg, note, consent = R.meaning_backend(p)
    if cfg is None:
        _refuse(p, note or R.NOTE_UNAVAILABLE)
    sections = all_sections()
    try:
        R.section_matrix(p, cfg, sections, docs_key(sections))
    except Exception:   # noqa: BLE001  a fixed sentence, as on the page
        _refuse(p, R.NOTE_FAILED)
    finally:
        p.close()
    print(f"Index built with {cfg.name}: {len(sections)} sections of WIDER's documents.")


@assistant_app.command("check-retrieval")
def check_retrieval_cmd(as_json: bool = JSON):
    """Ask WIDER's shipped question set of WIDER's own documents by keywords alone: per reader group, the share
    found in the first three, and the questions WIDER does not explain that matched anyway. Exit 0 when every group
    reaches 90% and none of those matched, else 1. Reads no project and calls no model."""
    from .assistant import evaluate as E

    r = E.check()
    if as_json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        for g in E.GROUPS:
            d = r["groups"].get(g, {"found": 0, "total": 0, "share": 0.0, "missed": []})
            print(f"{g}: {d['found']} of {d['total']} found in the first {r['top']} ({d['share']:.0%})")
            for q in d["missed"]:
                print(f"  missed: {q}")
        u = r["uncovered"]
        print(f"not explained by WIDER's documents: {len(u['matched'])} of {u['total']} matched a section")
        for q in u["matched"]:
            print(f"  matched: {q}")
        print("OK" if r["ok"] else f"Not OK: every group needs {r['min_share']:.0%} and no unexplained question may "
                                   "match.")
    if not r["ok"]:
        raise typer.Exit(1)
