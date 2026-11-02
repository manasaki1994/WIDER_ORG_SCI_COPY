"""Setup commands added to the existing CLI groups: LLM / embedding backends, seed papers, database choice.

``register(...)`` is called once from ``cli.py``; it attaches commands to the ``providers``, ``embed``,
``benchmark`` and ``query`` groups so the hot ``cli.py`` only grows by one call.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from .core.backends import STAGE_KEYS

DIR_OPT = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")


def _move_exit(p, e) -> None:
    """A move of shown validation papers needs --confirm (D3 of validation-reuse): the warning, nothing done, exit 2."""
    p.close()
    rprint(f"[yellow]{escape(e.notice)}[/yellow]")
    rprint(f"[red]{e.confirm}[/red]")
    raise typer.Exit(2)


def register(providers_app: typer.Typer, embed_app: typer.Typer, benchmark_app: typer.Typer, query_app: typer.Typer) -> None:
    # ------------------------------------------------------------ providers
    @providers_app.command("presets")
    def providers_presets(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")):
        """The backend catalogue: CLI tools, cloud APIs and local servers WIDER can set up for you."""
        import json

        from .providers.presets import preset_row, presets

        rows = [preset_row(x) for x in presets()]
        if as_json:
            typer.echo(json.dumps(rows, indent=2))
            return
        t = Table("preset", "family", "kind", "label", "default model", "billing")
        for r in rows:
            t.add_row(r["id"], r["family"], r["kind"], r["label"] + (" (experimental)" if r["experimental"] else ""),
                      r["default_model"] or "", r["billing"])
        rprint(t)

    @providers_app.command("detect")
    def providers_detect(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")):
        """Find backends on this computer. Sends nothing off the computer: it looks for programs, asks local servers
        on 127.0.0.1 and checks whether key variables are set (never their values)."""
        import json

        from .core.detect import detect

        rows = detect()
        if as_json:
            typer.echo(json.dumps(rows, indent=2, default=str))
            return
        t = Table("preset", "found", "where", "signed in", "key set", "models")
        for r in rows:
            key = "" if r.get("key_set") is None else ("yes" if r["key_set"] else "no")
            t.add_row(r["preset"], "yes" if r.get("found") else "no", str(r.get("where") or ""),
                      str(r.get("signed_in") or ""), key, ", ".join((r.get("models") or [])[:5]))
        rprint(t)

    @providers_app.command("models")
    def providers_models(
        name: str = typer.Argument(None, help="Saved backend name."),
        preset_id: str = typer.Option(None, "--preset", help="A preset id instead of a saved backend (local servers and CLI tools)."),
        kind: str = typer.Option(None, "--kind", help="Instead of a preset: a backend kind (openai_compat, ollama) "
                                                      "asked at --base-url, as the setup form filled in by hand."),
        base_url: str = typer.Option(None, "--base-url", help="With --preset or --kind: the server address."),
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
        directory: Path = DIR_OPT,
    ):
        """List the models a backend offers. A network, cloud or unknown backend is asked only with your consent
        (`wider providers allow NAME --for models`); only the key header is sent."""
        import json

        from .cli_backend_consent import ensure_backend_consent
        from .core.detect import list_models

        if [bool(name), bool(preset_id), bool(kind)].count(True) != 1:
            rprint("[red]name a saved backend, or give --preset or --kind[/red]")
            raise typer.Exit(1)
        try:
            if name:
                from .core.project import Project

                p = Project.find(directory)
                try:
                    try:
                        cfg = p.provider(name)
                    except KeyError:
                        rprint(f"[red]unknown provider {name}[/red]")
                        raise typer.Exit(1) from None
                    if cfg.kind not in ("cli_agent", "typesafe", "fake"):   # those list the catalogue; nothing is sent
                        from .cli_lookups import say_sent
                        say_sent(p, "models", cfg.name, quiet=as_json)
                        ensure_backend_consent(p, cfg, "models")
                    models = list_models(cfg, project=p)
                finally:
                    p.close()
            else:
                models = list_models(preset_id=preset_id, base_url=base_url, kind=kind)
        except (KeyError, ValueError) as e:
            rprint(f"[red]{str(e).strip(chr(39))}[/red]")
            raise typer.Exit(1) from None
        if as_json:
            typer.echo(json.dumps(models))
        else:
            for m in models:
                typer.echo(m)

    def _backend_options():
        """The settings shared by `providers add` and `providers edit` (None means not given)."""
        return dict(
            model=typer.Option(None, "--model", "-m", help="Model id as the backend names it."),
            base_url=typer.Option(None, "--base-url", help="Endpoint base URL (HTTP backends)."),
            api_key_env=typer.Option(None, "--api-key-env",
                                     help="NAME of the environment variable holding the API key; the value is never stored."),
            command=typer.Option(None, "--command",
                                 help="cli_agent only: argv template as a JSON list, e.g. "
                                      "'[\"claude\", \"-p\", \"--model\", \"{model}\", \"--system-prompt-file\", \"{system_file}\"]'. "
                                      "Placeholders: {model}, {system_file}, {system_toml}, {schema_file}, {system}, {user}; the user message goes "
                                      "to standard input."),
            extra=typer.Option(None, "--extra", help="Backend-specific setting key=value (JSON value or text; repeatable); "
                                                     "key= removes it. E.g. max_output_tokens=4096, auth=api-key."),
            timeout=typer.Option(None, "--timeout", help="Seconds before one call is given up."),
            concurrency=typer.Option(None, "--concurrency", help="Calls sent at the same time."),
            temperature=typer.Option(None, "--temperature", help="Sampling temperature (0 = most repeatable)."),
            price_in=typer.Option(None, "--price-in", help="US dollars per million input tokens (for cost display only)."),
            price_out=typer.Option(None, "--price-out", help="US dollars per million output tokens (for cost display only)."),
            price_cached_in=typer.Option(None, "--price-cached-in", help="US dollars per million cached input tokens."),
            price_cache_write=typer.Option(None, "--price-cache-write", help="US dollars per million tokens written to the cache."),
        )

    def _fields(timeout, concurrency, temperature, price_in, price_out, price_cached_in, price_cache_write, extra):
        from .core.backends import parse_extra_pairs

        return dict(timeout_s=timeout, max_concurrency=concurrency, temperature=temperature,
                    price_input_per_mtok=price_in, price_output_per_mtok=price_out,
                    price_cached_input_per_mtok=price_cached_in, price_cache_write_per_mtok=price_cache_write,
                    extra=parse_extra_pairs(extra) if extra else None)

    opt = _backend_options()

    @providers_app.command("add")
    def providers_add(
        name: str = typer.Option(None, "--name", "-n",
                                 help="Name used in project.yaml and --provider options; with --preset it defaults to the "
                                      "preset id or the first free <id>-N. An existing name is replaced."),
        preset_id: str = typer.Option(None, "--preset", help="Fill every setting from a preset (`wider providers presets`)."),
        kind: str = typer.Option(None, "--kind", "-k",
                                 help="Backend kind: openai_compat (default) | ollama | anthropic | cli_agent | litellm | typesafe."),
        model: str = opt["model"], base_url: str = opt["base_url"], api_key_env: str = opt["api_key_env"],
        command: str = opt["command"], extra: list[str] = opt["extra"], timeout: float = opt["timeout"],
        concurrency: int = opt["concurrency"], temperature: float = opt["temperature"], price_in: float = opt["price_in"],
        price_out: float = opt["price_out"], price_cached_in: float = opt["price_cached_in"],
        price_cache_write: float = opt["price_cache_write"],
        accept_experimental: bool = typer.Option(False, "--accept-experimental",
                                                 help="Acknowledge the limitations of an experimental preset."),
        set_default: bool = typer.Option(False, "--set-default", help="Make this the project's default provider."),
        directory: Path = DIR_OPT,
    ):
        """Add an AI model, from a preset or by hand. Adding over an existing name replaces it (settings not given
        go back to their defaults); `wider providers edit` changes some settings and keeps the rest. Keys are referenced
        by environment variable name only."""
        from rich.markup import escape

        from .core.backends import add_provider
        from .core.project import Project

        p = Project.find(directory)
        try:
            existed = {c.name for c in p.config.providers}
            fields = {k: v for k, v in _fields(timeout, concurrency, temperature, price_in, price_out, price_cached_in,
                                               price_cache_write, extra).items() if v is not None}
            cfg, reset = add_provider(p, name, kind=kind, model=model or "", base_url=base_url, api_key_env=api_key_env,
                                      command=command, set_default=set_default, preset=preset_id,
                                      accept_experimental=accept_experimental, **fields)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        rprint(f"[green]provider {escape(cfg.name)}[/green] ({cfg.kind}, {escape(cfg.model or 'no model')})"
               + (" is the default" if p.config.default_provider == cfg.name else ""))
        if cfg.name in existed:
            rprint(f"[yellow]replaced the existing {escape(cfg.name)}"
                   + (f"; reset to defaults: {', '.join(reset)}" if reset else "")
                   + f". To change some settings and keep the rest, use `wider providers edit {escape(cfg.name)}`.[/yellow]")
        elif preset_id and not name and cfg.name != preset_id:
            rprint(f"added as {escape(cfg.name)} ({escape(preset_id)} already exists; "
                   f"to change it use `wider providers edit {escape(preset_id)}`)")

    @providers_app.command("edit")
    def providers_edit(
        name: str = typer.Argument(..., help="Backend name."),
        model: str = opt["model"], base_url: str = opt["base_url"], api_key_env: str = opt["api_key_env"],
        command: str = opt["command"], extra: list[str] = opt["extra"], timeout: float = opt["timeout"],
        concurrency: int = opt["concurrency"], temperature: float = opt["temperature"], price_in: float = opt["price_in"],
        price_out: float = opt["price_out"], price_cached_in: float = opt["price_cached_in"],
        price_cache_write: float = opt["price_cache_write"],
        directory: Path = DIR_OPT,
    ):
        """Change some settings of a backend and keep all others (every `extra` key not named stays). An empty
        --base-url or --api-key-env clears it. The name and kind cannot be changed."""
        from rich.markup import escape

        from .core.backends import update_provider
        from .core.project import Project

        p = Project.find(directory)
        try:
            cfg = update_provider(p, name, model=model, base_url=base_url, api_key_env=api_key_env, command=command,
                                  **_fields(timeout, concurrency, temperature, price_in, price_out, price_cached_in,
                                            price_cache_write, extra))
        except KeyError:
            rprint(f"[red]no provider {escape(name)!r}[/red]")
            raise typer.Exit(1) from None
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        rprint(f"[green]provider {escape(cfg.name)} updated[/green] ({cfg.kind}, {escape(cfg.model or 'no model')})")

    @providers_app.command("remove")
    def providers_remove(name: str = typer.Argument(..., help="Provider name."), directory: Path = DIR_OPT):
        """Remove a provider; the default and stage overrides fall back to the first remaining one."""
        from .core.backends import remove_provider
        from .core.project import Project

        p = Project.find(directory)
        try:
            remove_provider(p, name)
        except KeyError:
            rprint(f"[red]no provider {name!r}[/red]")
            raise typer.Exit(1)
        rprint(f"removed {name}; default is now {p.config.default_provider or 'none'}")

    @providers_app.command("set-default")
    def providers_set_default(name: str = typer.Argument(..., help="Provider name."), directory: Path = DIR_OPT):
        """Make a configured provider the default for every stage without an override."""
        from .core.backends import set_default_provider
        from .core.project import Project

        p = Project.find(directory)
        try:
            set_default_provider(p, name)
        except KeyError:
            rprint(f"[red]no provider {name!r}[/red]")
            raise typer.Exit(1)
        rprint(f"default provider: {name}")

    @providers_app.command("stage")
    def providers_stage(
        stage: str = typer.Argument(..., help="Stage key: " + " | ".join(STAGE_KEYS) + "."),
        name: str = typer.Argument(None, help="Provider name; omit to clear the override."),
        name2: str = typer.Argument(None, help="review only: the second reader (omit: the first reads twice)."),
        directory: Path = DIR_OPT,
    ):
        """Use a specific provider for one stage (construct proposals, screening, the second reader of the strings).

        `review NAME [NAME2]` sets the second reader's one or two backends; without NAME2 the one reads twice, without
        a name the construct backend reads twice."""
        from .core.backends import set_review_readers, set_stage_provider
        from .core.project import Project

        p = Project.find(directory)
        try:
            if stage.strip() == "review":
                set_review_readers(p, [name, name2])
            elif name2:
                rprint(f"[red]only the review stage takes a second name; {stage!r} takes one[/red]")
                raise typer.Exit(1)
            else:
                set_stage_provider(p, stage, name)
        except KeyError:
            missing = next(n for n in (name, name2) if n and n not in {c.name for c in p.config.providers})
            rprint(f"[red]no provider {missing!r}[/red]")
            raise typer.Exit(1)
        except ValueError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if stage.strip() == "review":
            rprint("review: " + (f"{name}, {name2}" if name and name2 else f"{name} (reads twice)" if name
                                 else "the construct backend (reads twice)"))
        else:
            rprint(f"{stage}: {name or 'default provider'}")

    # ------------------------------------------------------------ embeddings
    @embed_app.command("add")
    def embed_add(
        name: str = typer.Option(None, "--name", "-n",
                                 help="Embedding name used in project.yaml; with --preset it defaults to the preset id."),
        preset_id: str = typer.Option(None, "--preset", help="Fill every setting from an embedding preset."),
        kind: str = typer.Option(None, "--kind", "-k",
                                 help="Backend kind: sentence_transformers (default) | openai | openai_compat | ollama."),
        model: str = typer.Option("", "--model", "-m", help="Model id (Hugging Face id for sentence_transformers)."),
        dim: int = typer.Option(None, "--dim", help="Truncate vectors to this many dimensions (omit for the model's full size)."),
        base_url: str = typer.Option(None, "--base-url", help="Endpoint base URL (HTTP backends)."),
        api_key_env: str = typer.Option(None, "--api-key-env", help="NAME of the environment variable holding the API key."),
        batch_size: int = typer.Option(None, "--batch-size", help="Texts sent in one request."),
        set_default: bool = typer.Option(False, "--set-default", help="Make this the project's default embedding."),
        price_in: float = typer.Option(None, "--price-in", help="US dollars per million input tokens (for cost display only)."),
        directory: Path = DIR_OPT,
    ):
        """Add or replace an embedding model, from a preset or by hand. Local models are not downloaded until
        'wider embed download'; `wider embed edit` changes some settings and keeps the rest."""
        from rich.markup import escape

        from .core.backends import add_embedding
        from .core.project import Project

        p = Project.find(directory)
        try:
            existed = {c.name for c in p.config.embeddings}
            cfg, reset = add_embedding(p, name, kind=kind, model=model, dim=dim, base_url=base_url,
                                       api_key_env=api_key_env, set_default=set_default, preset=preset_id,
                                       price_input_per_mtok=price_in, batch_size=batch_size)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        rprint(f"[green]embedding {escape(cfg.name)}[/green] ({cfg.kind}, {escape(cfg.model)})")
        if cfg.name in existed:
            rprint(f"[yellow]replaced the existing {escape(cfg.name)}"
                   + (f"; reset to defaults: {', '.join(reset)}" if reset else "")
                   + f". To change some settings and keep the rest, use `wider embed edit {escape(cfg.name)}`.[/yellow]")

    @embed_app.command("edit")
    def embed_edit(
        name: str = typer.Argument(..., help="Embedding name."),
        model: str = typer.Option(None, "--model", "-m", help="Model id."),
        dim: int = typer.Option(None, "--dim", help="Truncate vectors to this many dimensions."),
        base_url: str = typer.Option(None, "--base-url", help="Endpoint base URL; empty clears it."),
        api_key_env: str = typer.Option(None, "--api-key-env", help="NAME of the key variable; empty clears it."),
        batch_size: int = typer.Option(None, "--batch-size", help="Texts sent in one request."),
        price_in: float = typer.Option(None, "--price-in", help="US dollars per million input tokens."),
        directory: Path = DIR_OPT,
    ):
        """Change some settings of an embedding model and keep the others. The name and kind cannot be changed."""
        from rich.markup import escape

        from .core.backends import update_embedding
        from .core.project import Project

        p = Project.find(directory)
        try:
            cfg = update_embedding(p, name, model=model, dim=dim, base_url=base_url, api_key_env=api_key_env,
                                   batch_size=batch_size, price_input_per_mtok=price_in)
        except KeyError:
            rprint(f"[red]no embedding {escape(name)!r}[/red]")
            raise typer.Exit(1) from None
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        rprint(f"[green]embedding {escape(cfg.name)} updated[/green] ({cfg.kind}, {escape(cfg.model)})")

    @embed_app.command("remove")
    def embed_remove(name: str = typer.Argument(..., help="Embedding name."), directory: Path = DIR_OPT):
        """Remove an embedding model from the project."""
        from .core.backends import remove_embedding
        from .core.project import Project

        p = Project.find(directory)
        try:
            remove_embedding(p, name)
        except KeyError:
            rprint(f"[red]no embedding {name!r}[/red]")
            raise typer.Exit(1)
        rprint(f"removed {name}; default is now {p.config.default_embedding or 'none'}")

    @embed_app.command("set-default")
    def embed_set_default(name: str = typer.Argument(..., help="Embedding name."), directory: Path = DIR_OPT):
        """Make a configured embedding model the project default."""
        from .core.backends import set_default_embedding
        from .core.project import Project

        p = Project.find(directory)
        try:
            set_default_embedding(p, name)
        except KeyError:
            rprint(f"[red]no embedding {name!r}[/red]")
            raise typer.Exit(1)
        rprint(f"default embedding: {name}")

    @embed_app.command("download")
    def embed_download(name: str = typer.Argument(..., help="Embedding name (sentence_transformers kind)."),
                       directory: Path = DIR_OPT):
        """Download a local model's weights now (the only command that triggers a download)."""
        from .core.backends import download_embedding
        from .core.project import Project

        p = Project.find(directory)
        try:
            r = download_embedding(p, name)
        except KeyError:
            rprint(f"[red]no embedding {name!r}[/red]")
            raise typer.Exit(1)
        rprint(f"[green]{name} ready[/green] ({r.get('dim')} dims)" if r.get("ok") else f"[red]{r.get('error')}[/red]")
        if not r.get("ok"):
            raise typer.Exit(1)

    # ------------------------------------------------------------ seed papers
    @benchmark_app.command("add")
    def benchmark_add(
        doi: list[str] = typer.Option(None, "--doi", help="DOI of a seed paper (repeatable)."),
        title: list[str] = typer.Option(None, "--title", help="Title of a seed paper (repeatable); resolved to a DOI later."),
        file: Path = typer.Option(None, "--file", help="CSV (title, doi, year, first_author, set) or RIS export to add."),
        set_name: str = typer.Option("Development", "--set", help="Development | Validation."),
        resolve: bool = typer.Option(False, "--resolve", help="Look the new papers up on OpenAlex / Crossref right away."),
        source: str = typer.Option("researcher", "--source",
                                   help="Where the papers came from: prior_review | researcher | colleague | "
                                        "other_database | query_results | seed_chain:<id>."),
        from_candidate: int = typer.Option(None, "--from-candidate",
                                           help="Add candidate N of `wider benchmark candidates` (source seed_chain:<id>)."),
        directory: Path = DIR_OPT,
    ):
        """Add seed (benchmark) papers by DOI, title or file; stored in benchmark.csv and the store."""
        from .construct import seed
        from .core.project import Project

        try:
            source = seed.source_name(source)
        except ValueError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        p = Project.find(directory)
        if from_candidate is not None:
            from .seeds import chain

            try:
                a, notices = chain.add(p, from_candidate, set_name)
            except (KeyError, ValueError) as e:
                rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
                p.close()
                raise typer.Exit(1)
            rprint(f"[green]{a.benchmark_id}[/green] {a.set} {a.source} {a.doi or ''} {escape(a.title[:70])}")
            for n in notices:
                rprint(f"[yellow]{escape(n)}[/yellow]")
            p.close()
            return
        if resolve:   # asked before anything is added, so a refusal leaves benchmark.csv unchanged
            from .cli_lookups import ensure_consent
            ensure_consent(p, "seed_resolve", (len(doi or []) + len(title or [])) or None,
                           hint="Nothing was added. Add the papers without --resolve and run `wider benchmark resolve` "
                                "after allowing online lookups.")
        added: list = []
        skipped: list[dict] = []
        try:
            if doi:
                added += seed.add_by_dois(p, list(doi), set_name, source, skipped)
            if title:
                added += seed.add_by_titles(p, list(title), set_name, source, skipped)
            if file:
                added += seed.add_from_file(p, file, set_name, source, skipped)
        except ValueError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        for s in skipped:   # already seeds or repeated rows are named, never dropped silently (M8)
            rprint(f"[yellow]skipped {escape(s['label'])}: {escape(s['reason'])}[/yellow]")
        if not added:
            if skipped:
                rprint(f"[yellow]nothing added: {seed.skipped_summary(skipped)}[/yellow]")
            else:
                rprint("[yellow]nothing added: give --doi, --title or --file[/yellow]")
            p.close()
            raise typer.Exit(1)
        for a in added:
            rprint(f"[green]{a.benchmark_id}[/green] {a.set} {a.source} {a.doi or ''} {a.title[:70]}")
            for n in seed.set_notices(p, a):
                rprint(f"[yellow]{escape(n)}[/yellow]")
        if skipped:
            rprint(f"[yellow]{len(added)} added; {seed.skipped_summary(skipped)}[/yellow]")
        if resolve:
            _print_resolve(seed.resolve(p))
        p.close()

    # ------------------------------------------------------------ candidates from the seed papers (2c, D16)
    @benchmark_app.command("chain")
    def benchmark_chain(
        direction: str = typer.Option("both", "--direction", help="references | cited_by | both."),
        limit: int = typer.Option(None, "--limit", help="Chain at most N Development seeds."),
        from_candidates: bool = typer.Option(False, "--from-candidates", help="A snowball round: chain only the "
                                             "Development seeds you took from candidates, in the directions not "
                                             "chained yet."),
        directory: Path = DIR_OPT,
    ):
        """List the works the Development seed papers cite and the works citing them, as candidates (OpenAlex,
        under the online-lookup consent). Sends the seeds' DOIs and OpenAlex ids; Validation seeds are never sent.
        Nothing is added to the seed papers: add a candidate with `wider benchmark add --from-candidate N`."""
        from .cli_lookups import ensure_consent
        from .core.project import Project
        from .seeds import chain

        if direction not in chain.DIRECTIONS:
            rprint("[red]--direction must be references, cited_by or both[/red]")
            raise typer.Exit(1)
        p = Project.find(directory)
        ensure_consent(p, "seed_chain", hint="Nothing was sent.")
        r = chain.chain_seeds(p, direction, limit, from_candidates=from_candidates)
        rprint(f"round {r['round']}: chained {r['seeds_chained']} Development seed(s) ({direction}): {r['found']} "
               f"works found, {r['new']} new candidate(s)")
        for m in r["messages"]:
            rprint(f"[yellow]{escape(m)}[/yellow]")
        _print_candidates(r["candidates"])
        p.close()

    @benchmark_app.command("references")
    def benchmark_references(
        benchmark_id: str = typer.Argument(None, metavar="[ID]", help="One Development seed (default: all)."),
        route: str = typer.Option("auto", "--route", help="auto (OpenAlex when online lookups are on, and the PDF) "
                                  "| openalex | pdf (sends nothing)."),
        pdf: Path = typer.Option(None, "--pdf", help="Read this PDF as the seed's PDF (needs ID).", exists=True,
                                 dir_okay=False),
        tier: str = typer.Option(None, "--tier", help="Parse the PDF entries further: grobid (a GROBID server on "
                                 "this computer) | crossref (titles only, under the online-lookup consent) | model "
                                 "(the construct backend; keeps only what the entry says)."),
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
        directory: Path = DIR_OPT,
    ):
        """The reference lists of the Development seed papers: OpenAlex first, the seed's PDF as the fallback, and
        the entries the PDF has and OpenAlex lacks. A reference DOI OpenAlex did not list becomes a candidate with
        origin pdf. Validation seeds are never processed."""
        import json

        from .cli_backend_consent import ensure_backend_consent
        from .cli_lookups import ensure_consent
        from .core.project import Project
        from .seeds import references

        if route not in references.ROUTES or (tier is not None and tier not in references.TIERS):
            rprint("[red]--route must be auto, openalex or pdf; --tier grobid, crossref or model[/red]")
            raise typer.Exit(1)
        p = Project.find(directory)
        if route == "openalex":
            ensure_consent(p, "seed_refs", hint="Nothing was sent.", quiet=as_json)
        if tier == "crossref":
            ensure_consent(p, "ref_match", hint="Nothing was sent.", quiet=as_json)
        if tier == "model":
            from .cli_lookups import say_sent
            say_sent(p, "seed_references", quiet=as_json)
        for cfg, op in references.needs_consent(p, tier):
            ensure_backend_consent(p, cfg, op)
        try:
            r = references.seed_references(p, benchmark_id, route, pdf, tier)
        except (KeyError, ValueError) as e:
            rprint(f"[red]{escape(str(e.args[0] if isinstance(e, KeyError) else e))}[/red]")
            raise typer.Exit(1)
        finally:
            p.close()
        if as_json:
            print(json.dumps(r, indent=2, default=str))
            return
        for line in references.lines(r):
            rprint(escape(line))

    @benchmark_app.command("candidates")
    def benchmark_candidates(
        dismiss: int = typer.Option(None, "--dismiss", help="Dismiss candidate N (kept, marked dismissed)."),
        round_no: int = typer.Option(None, "--round", help="Only round K (0 = from a file, 1 = from the seeds, "
                                     "2 and up = snowball rounds)."),
        import_file: Path = typer.Option(None, "--import", help="An export of any tool (RIS, CSV, BibTeX) to add as "
                                         "candidates; nothing is sent.", exists=True, dir_okay=False),
        origin: str = typer.Option(None, "--origin", help="Where the --import file came from, e.g. citationchaser."),
        dismiss_import: int = typer.Option(None, "--dismiss-import", help="Dismiss the candidates import N added "
                                           "that are still new."),
        as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
        directory: Path = DIR_OPT,
    ):
        """The candidates from the seed papers and from imported files, ranked by links to the seeds: new and added
        first, dismissed last."""
        import json

        from .core.project import Project
        from .seeds import chain

        p = Project.find(directory)
        if import_file is not None:
            if not origin:
                rprint("[red]--import needs --origin LABEL (where the file came from, e.g. citationchaser)[/red]")
                p.close()
                raise typer.Exit(1)
            try:
                r = chain.import_candidates(p, import_file, origin)
            except ValueError as e:
                rprint(f"[red]{escape(str(e))}[/red]")
                p.close()
                raise typer.Exit(1)
            head = f"import {r['import_no']}: " if r["import_no"] else ""
            rprint(f"{head}{r['n_records']} record(s) from {escape(r['file'])} ({escape(r['origin'])}): {r['new']} new "
                   f"candidate(s), {r['merged']} known candidate(s) gained the origin; nothing was sent")
            for m in r["messages"]:
                rprint(f"[yellow]{escape(m)}[/yellow]")
        if dismiss_import is not None:
            try:
                r = chain.dismiss_import(p, dismiss_import)
            except KeyError as e:
                rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
                p.close()
                raise typer.Exit(1)
            rprint(f"import {r['import_no']} ({escape(r['origin'])}): {r['dismissed']} candidate(s) dismissed")
        if dismiss is not None:
            try:
                c = chain.dismiss(p, dismiss)
            except (KeyError, ValueError) as e:
                rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
                p.close()
                raise typer.Exit(1)
            rprint(f"candidate {c['candidate_id']} dismissed by {c['by'] or 'unknown'} at {c['at']}")
        rows = chain.candidates(p, round_no)
        if as_json:
            out = {"candidates": rows, "rounds": chain.rounds(p), "stop_notice": chain.stop_notice(p),
                   "imports": chain.imports(p)}
            p.close()
            typer.echo(json.dumps(out, indent=2))
            return
        _print_candidates(rows, chain.stop_notice(p))
        p.close()

    def _print_candidates(rows, stop=None):
        from .seeds import chain

        if not rows:
            rprint("no candidates yet: run `wider benchmark chain` or import a file with "
                   "`wider benchmark candidates --import FILE --origin LABEL`")
            return
        rprint(f"[dim]{chain.RANK_NOTE}[/dim]")
        t = Table("rank", "n", "status", "linking seeds", "link", "round", "origin", "year", "doi", "title")
        for r in rows:
            status = r["status"] + (f" as {r['benchmark_id']}" if r["status"] == "added" else "")
            title = escape((r["title"] or "")[:60]) + ("  [cyan]retrieved by the query[/cyan]" if r["retrieved"] else "") + (
                "  [green]good validation candidate[/green]" if r.get("good_validation") else "")
            t.add_row(str(r["rank"]), str(r["candidate_id"]), status, ", ".join(r["linking_seeds"]),
                      r["direction"] or "", str(r["chain_round"] if r["chain_round"] is not None else ""),
                      escape(", ".join(r["origins"])), str(r["year"] or ""), r["doi"] or "", title)
        rprint(t)
        if stop:
            rprint(f"[yellow]{escape(stop)}[/yellow]")

    def _print_resolve(rows):
        t = Table("id", "status", "source", "doi", "title")
        for r in rows:
            colour = {"resolved": "green", "unresolved": "red"}.get(r["resolved"], "yellow")
            t.add_row(r["benchmark_id"], f"[{colour}]{r['resolved']}[/{colour}]", r.get("resolve_source") or "",
                      r["doi"] or "", (r["title"] or "")[:60] + (f"  [dim]dup of {r['duplicate_of']}[/dim]" if r.get("duplicate_of") else ""))
        rprint(t)
        for r in rows:   # resolve may fill in a DOI that matches an import: the check runs again (D4)
            if r.get("looked_up", True):
                for n in r.get("notices") or []:
                    rprint(f"[yellow]{escape(n)}[/yellow]")

    @benchmark_app.command("resolve")
    def benchmark_resolve(
        all_: bool = typer.Option(False, "--all", help="Re-resolve papers that already resolved."),
        directory: Path = DIR_OPT,
    ):
        """Resolve seed papers on OpenAlex, then Crossref: fill DOI, year, first author, abstract; flag the rest."""
        from .cli_lookups import ensure_consent
        from .construct import seed
        from .core.project import Project

        p = Project.find(directory)
        ensure_consent(p, "seed_resolve", len(p.read_benchmark()))
        rows = seed.resolve(p, only_unresolved=not all_,
                            progress=lambda i, n, r: rprint(f"{i}/{n} {r['benchmark_id']} {r['status']}"))
        _print_resolve(rows)
        rprint(seed.resolve_summary(rows)["summary"])
        p.close()

    @benchmark_app.command("split")
    def benchmark_split(
        dev_share: str = typer.Option(None, "--dev-share",
                                      help="Share of seed papers for the development set (default: protocol identification.development_share)."),
        seed_: int = typer.Option(None, "--seed", help="Random seed (default: the project seed)."),
        apply: bool = typer.Option(False, "--apply", help="Write the suggested assignment to benchmark.csv."),
        confirm: bool = typer.Option(False, "--confirm", help="Apply it although it moves validation papers that have "
                                     "been shown to Development (they become former validation papers; final)."),
        directory: Path = DIR_OPT,
    ):
        """Suggest a seeded random development / validation split; nothing changes unless --apply."""
        from .construct import seed
        from .core.project import Project

        p = Project.find(directory)
        try:
            s = seed.suggest_split(p, dev_share=dev_share, seed=seed_)
        except ValueError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        rprint(f"[bold]{len(s['development'])} development / {len(s['validation'])} validation[/bold] "
               f"of {s['n']} (dev share {s['dev_share']:.2f}, seed {s['seed']})")
        rprint(f"[dim]{s['rule']}[/dim]")
        rprint("development:", ", ".join(s["development"]))
        rprint("validation:", ", ".join(s["validation"]))
        if apply:
            try:
                seed.apply_split(p, s, confirm=confirm)
            except seed.MoveNeedsConfirm as e:
                _move_exit(p, e)
            rprint("[green]applied[/green]")
        p.close()

    @benchmark_app.command("set")
    def benchmark_set(
        benchmark_id: str = typer.Argument(..., help="Benchmark id (B001, ...)."),
        set_name: str = typer.Argument(..., help="Development | Validation."),
        confirm: bool = typer.Option(False, "--confirm", help="Move a validation paper that has been shown to "
                                     "Development: it becomes a former validation paper, reported; the move is final."),
        directory: Path = DIR_OPT,
    ):
        """Move one seed paper to the development or the validation set (journalled). A validation paper that has been
        shown moves to Development only with --confirm."""
        from .construct import seed
        from .core.project import Project

        p = Project.find(directory)
        try:
            bp = seed.set_assignment(p, benchmark_id, set_name, confirm=confirm)
        except KeyError:
            p.close()
            rprint(f"[red]no benchmark paper {benchmark_id!r}[/red]")
            raise typer.Exit(1)
        except seed.MoveNeedsConfirm as e:
            _move_exit(p, e)
        rprint(f"{bp.benchmark_id}: {bp.set}")
        for n in seed.set_notices(p, bp):
            rprint(f"[yellow]{escape(n)}[/yellow]")
        p.close()

    @benchmark_app.command("move-used")
    def benchmark_move_used(
        confirm: bool = typer.Option(False, "--confirm", help="Move them: they become former validation papers, "
                                     "reported; the move is final."),
        directory: Path = DIR_OPT,
    ):
        """Move every used validation paper (shown, no longer independent) to Development, then add new validation
        papers from sources independent of the query."""
        from .construct import seed
        from .core.project import Project

        p = Project.find(directory)
        try:
            ids = seed.move_used(p, confirm=confirm)
        except seed.MoveNeedsConfirm as e:
            _move_exit(p, e)
        except ValueError as e:
            p.close()
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)
        p.close()
        rprint(f"moved to Development: {', '.join(ids)}")
        rprint(f"[yellow]{escape(seed.ADD_INVITE)}[/yellow]")

    @benchmark_app.command("source")
    def benchmark_source(
        benchmark_id: str = typer.Argument(..., help="Benchmark id (B001, ...)."),
        source: str = typer.Argument(..., help="prior_review | researcher | colleague | other_database | "
                                               "query_results | seed_chain:<id>."),
        directory: Path = DIR_OPT,
    ):
        """Change where one seed paper came from (journalled; `wider history undo` restores it)."""
        from .construct import seed
        from .core.project import Project

        p = Project.find(directory)
        try:
            bp = seed.set_source(p, benchmark_id, source)
        except KeyError:
            rprint(f"[red]no benchmark paper {benchmark_id!r}[/red]")
            raise typer.Exit(1)
        except ValueError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        finally:
            p.close()
        rprint(f"{bp.benchmark_id}: {bp.source}")

    @benchmark_app.command("keywords")
    def benchmark_keywords(
        benchmark_id: str = typer.Argument(..., help="Benchmark id (B001, ...)."),
        keywords: str = typer.Argument(..., help='Author keywords separated by ";" (e.g. "voice; silence"); "" clears them.'),
        directory: Path = DIR_OPT,
    ):
        """Type a seed paper's keywords, read by the dry run for a block that searches keywords (journalled;
        `wider history undo` restores them). An import fills empty keywords from records with the same DOI ("from import")."""
        from .construct import seed
        from .core.project import Project

        p = Project.find(directory)
        try:
            bp = seed.set_keywords(p, benchmark_id, keywords)
        except KeyError:
            rprint(f"[red]no benchmark paper {benchmark_id!r}[/red]")
            raise typer.Exit(1)
        finally:
            p.close()
        rprint(escape(f"{bp.benchmark_id}: {bp.keywords} (typed)") if bp.keywords else f"{bp.benchmark_id}: no keywords")

    @benchmark_app.command("remove")
    def benchmark_remove(benchmark_id: str = typer.Argument(..., help="Benchmark id (B001, ...)."), directory: Path = DIR_OPT):
        """Remove a seed paper from benchmark.csv and the store."""
        from .construct import seed
        from .core.project import Project

        p = Project.find(directory)
        try:
            seed.remove(p, benchmark_id)
        except KeyError:
            rprint(f"[red]no benchmark paper {benchmark_id!r}[/red]")
            raise typer.Exit(1)
        rprint(f"removed {benchmark_id}")
        p.close()

    # ------------------------------------------------------------ databases
    platforms_app = typer.Typer(help="Databases to search: the catalogue and the project's ordered choice.",
                                invoke_without_command=True)
    query_app.add_typer(platforms_app, name="platforms")

    @platforms_app.callback()
    def platforms_list(ctx: typer.Context, directory: Path = DIR_OPT):
        """List the catalogue (coverage, export caps) and mark the platforms this project searches."""
        if ctx.invoked_subcommand:
            return
        from .core.project import Project
        from .search.catalogue import catalogue, project_platforms

        chosen = {}
        try:
            chosen = {c["key"]: c for c in project_platforms(Project.find(directory))}
        except Exception:
            pass  # no project here: show the catalogue alone
        from .search.evaluate import profiled_platforms

        prof = set(profiled_platforms())   # the dry run reads a platform only with a profile (D6)
        t = Table("key", "platform", "role", "coverage", "export", "cap", "profile")
        for c in catalogue():
            role = chosen.get(c["key"], {}).get("role", "")
            t.add_row(c["key"], c["label"], f"[bold]{role}[/bold]" if role else "", (c["coverage"] or "")[:60],
                      ", ".join(c.get("export_formats") or [])[:40], c.get("cap_text") or "-",
                      "yes" if c["key"] in prof else "no")
        for c in chosen.values():
            if c["custom"]:
                t.add_row(c["key"], c["label"] + " (custom)", f"[bold]{c['role']}[/bold]", f"syntax: {c['syntax']}", "", "",
                          "no")
        rprint(t)

    from .search.catalogue import catalogue
    keys = ", ".join(c["key"] for c in catalogue())   # the help names every catalogue platform

    @platforms_app.command("add")
    def platforms_add(
        key: str = typer.Argument(..., help=f"Catalogue key ({keys}) or a new key."),
        label: str = typer.Option(None, "--label", help="Display name (custom platforms)."),
        syntax: str = typer.Option(None, "--syntax", help="Syntax family to render with, for a platform not in the catalogue."),
        role: str = typer.Option(None, "--role", help="development | validation (default: first platform is development)."),
        directory: Path = DIR_OPT,
    ):
        """Add a platform to the project's search list."""
        from .core.project import Project
        from .search.catalogue import add_platform

        try:
            pl = add_platform(Project.find(directory), key, label=label, syntax=syntax, role=role)
        except ValueError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        rprint(f"[green]{pl.key}[/green] {pl.label} ({pl.role})")

    @platforms_app.command("remove")
    def platforms_remove(key: str = typer.Argument(..., help="Platform key."), directory: Path = DIR_OPT):
        """Remove a platform from the project's search list."""
        from .core.project import Project
        from .search.catalogue import remove_platform

        try:
            remove_platform(Project.find(directory), key)
        except KeyError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        rprint(f"removed {key}")

    @platforms_app.command("order")
    def platforms_order(keys: list[str] = typer.Argument(..., help="Platform keys in the new search order."),
                        directory: Path = DIR_OPT):
        """Reorder the search list (unlisted platforms keep their order after the listed ones)."""
        from .core.project import Project
        from .search.catalogue import order_platforms

        try:
            ordered = order_platforms(Project.find(directory), list(keys))
        except KeyError as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        rprint(" > ".join(p.key for p in ordered))

    @platforms_app.command("role")
    def platforms_role(key: str = typer.Argument(..., help="Platform key."),
                       role: str = typer.Argument(..., help="development | validation."), directory: Path = DIR_OPT):
        """Set which platform the query is developed on; the others validate it."""
        from .core.project import Project
        from .search.catalogue import set_role

        try:
            pl = set_role(Project.find(directory), key, role)
        except (KeyError, ValueError) as e:
            rprint(f"[red]{e}[/red]")
            raise typer.Exit(1)
        rprint(f"{pl.key}: {pl.role}")
