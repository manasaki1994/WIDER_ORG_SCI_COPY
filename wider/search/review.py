"""PRESS second reader (change 2c, D15): one or two model readers read each rendered string against the PRESS
checklist (McGowan et al. 2016), given the query tree, the string, its translation ledger, the lint and string-check
findings and the researcher's query decisions; no seed-paper passage.

Each reader runs once per platform (one reader runs twice). A finding is kept only when its quote is in the string
(whitespace collapsed, case ignored); the rest are counted as "N dropped: quote not found". Findings both runs report
(same PRESS item, one quote inside the other) are "agreed", the others "unsure"; both are shown (inform, not control).
Findings go to ``review_findings`` and the run's ``review.json`` and change nothing: no string, no synonyms version.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PRESS_ITEMS: dict[str, str] = {
    "translation": "Translation of the research question: do the blocks and their combination say what the question asks?",
    "operators": "Boolean and proximity operators: AND, OR, NOT, nesting and proximity distances",
    "subject_headings": "Subject headings: are controlled-vocabulary terms missing, too broad, too narrow or unexploded?",
    "text_words": "Text word searching (free text): missing synonyms, spelling variants, truncation and field codes",
    "spelling_syntax": "Spelling, syntax and line numbers: typos, quotes, brackets, operator words",
    "limits_filters": "Limits and filters: are they suitable and do they leave out relevant records?",
}
SEVERITIES = ("revision", "suggestion")   # PRESS: "revision(s) required" and "suggestion"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["findings"], "properties": {
    "findings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["item", "quote", "note", "severity"],
        "properties": {"item": {"type": "string", "enum": list(PRESS_ITEMS)},
                       "quote": {"type": "string"}, "note": {"type": "string"},
                       "severity": {"type": "string", "enum": list(SEVERITIES)}}}}}}
SYSTEM = ("You are a second reader of a literature search, using the PRESS checklist (Peer Review of Electronic "
          "Search Strategies). Read the search string for the named platform and report problems under the PRESS "
          "items. For each finding give the PRESS item key, a quote copied exactly from the search string (the "
          "words the finding is about), a short note saying what to check or change, and the severity: "
          "\"revision\" when the string is likely to miss or wrongly include records, \"suggestion\" otherwise. "
          "Quote only text that is in the search string. Report nothing you cannot tie to a quote. The researcher's "
          "decisions are deliberate; do not report them as problems. Answer with JSON only.")
USAGE_STAGE = "press_review"
RUN_KIND = "query/review"
NOTICE_SAME_MODEL = "same model twice: both runs use {model}, so agreement says less than two different models would"
NOTICE_SAME_FAMILY = ("same family twice: {a} and {b} are both {family} models, so agreement says less than two "
                      "families would")


class ReaderRefused(ValueError):
    """A reader that may not be given the strings (a CLI agent whose tools are not disabled); nothing was sent."""


def norm(s: str) -> str:
    """Whitespace collapsed and case folded, for the quote check and matching."""
    return " ".join((s or "").split()).casefold()


def family_of(cfg) -> str:
    """The model family: the leading letters of the model name after its last ``/``, lower-cased (``llama3.1:8b`` and
    ``meta-llama/Llama-3.1-8B`` → ``llama``). A model name without letters first falls back to the whole name."""
    model = (getattr(cfg, "model", None) or "").rsplit("/", 1)[-1].lower()
    m = re.match(r"[a-z]+", model)
    return m.group(0) if m else (model or cfg.name)


def model_id(cfg) -> str:
    return (getattr(cfg, "model", None) or "").rsplit("/", 1)[-1].lower() or cfg.name


def reader_label(cfg, run: int) -> str:
    return f"{cfg.name}/{cfg.model or ''}#{run}"


# ------------------------------------------------------------------ readers
def stage_readers(project) -> tuple[list[str], str]:
    """The reader names and where they come from: the ``review`` stage (``review`` and ``review_2``), else the
    construct backend. ``config.stage_providers`` is read directly (no fallback to the default provider for review);
    the construct backend is the one construct proposals use."""
    sp = project.config.stage_providers
    names = [n for n in (sp.get("review"), sp.get("review_2")) if n]
    if names:
        return names, "review stage"
    try:
        cfg = project.provider_for("construct")
    except (KeyError, TypeError, ValueError):
        cfg = None
    return ([cfg.name] if cfg else []), "construct backend"


def resolve_readers(project, names: list[str] | None = None) -> dict[str, Any]:
    """``{configs: [cfg1, cfg2], source, families, notices}``: two runs, one reader running twice when one is named.
    Raises ValueError for no reader, more than two, an unknown name or a backend that writes no text."""
    from ..providers.registry import require_text

    source = "--reader"
    if not names:
        names, source = stage_readers(project)
    names = [n for n in names if n]
    if not names:
        raise ValueError("no reader: choose one with `wider providers stage review NAME` (or --reader NAME), or set a "
                         "construct backend")
    if len(names) > 2:
        raise ValueError(f"at most two readers, got {len(names)}")
    cfgs = []
    for n in names:
        try:
            cfgs.append(require_text(project.provider(n)))
        except (KeyError, TypeError):
            raise ValueError(f"the reader {n!r} is not in project.yaml (choose another with `wider providers stage "
                             "review NAME`)") from None
    if len(cfgs) == 1:
        cfgs = cfgs * 2
    notices = []
    a, b = cfgs
    if model_id(a) == model_id(b):
        notices.append(NOTICE_SAME_MODEL.format(model=a.model or a.name))
    elif family_of(a) == family_of(b):
        notices.append(NOTICE_SAME_FAMILY.format(a=a.model, b=b.model, family=family_of(a)))
    return {"configs": cfgs, "source": source, "families": [family_of(c) for c in cfgs], "notices": notices}


def reader_rows(project, names: list[str] | None = None) -> dict[str, Any]:
    """The readers as the "Second reader" card shows them before a run: name, model, family and where each runs
    (from the backend check's assessment; nothing is sent). An unresolvable choice gives ``error``."""
    from ..core.backends import backend_place

    try:
        r = resolve_readers(project, names)
    except ValueError as e:
        return {"readers": [], "error": str(e), "notices": [], "source": None}
    rows = []
    for i, cfg in enumerate(r["configs"], 1):
        place = backend_place(project, cfg)
        rows.append({"run": i, "name": cfg.name, "kind": cfg.kind, "model": cfg.model, "family": family_of(cfg),
                     "locality": place.get("locality"), "locality_label": place.get("locality_label"),
                     "refused": _gate(cfg)})
    return {"readers": rows, "notices": r["notices"], "source": r["source"], "error": None}


def _gate(cfg) -> str | None:
    from ..providers.gate import tool_gate

    why = tool_gate(cfg)
    return f"the reader is refused: {why}, so nothing is sent to it" if why else None


# ------------------------------------------------------------------ prompt
def targets(project, query, platform: str | None = None) -> dict[str, dict[str, Any]]:
    """Per platform key: the renderer style, the rendered string, its ledger rows, the lint findings for that platform
    and the string check's findings. ``platform`` (a project or catalogue key) narrows to one."""
    from .catalogue import style_for
    from .lint import lint, lint_style
    from .render import platforms, render
    from .string_check import check_string
    from .translation import ledger

    if project.config.platforms:
        styles = {p.key: style_for(p) for p in project.config.platforms}
    else:
        styles = {k: k for k in ("ebsco", "scopus", "wos", "proquest", "ovid")}
    if platform:
        if platform in styles:
            styles = {platform: styles[platform]}
        elif platform in platforms():
            styles = {platform: platforms()[platform].get("style") or platform}
        else:
            raise ValueError(f"unknown platform {platform!r}; known: {', '.join(styles)}")
    out = {}
    for key, style in styles.items():
        s = render(query, style, pretty=True)
        boolean = style != "gscholar"
        out[key] = {"style": style, "string": s, "ledger": ledger(query, style),
                    "lint": lint(query, lint_style(project, key)),
                    "check": check_string(s, style) if boolean else []}
    return out


def prompt(query, key: str, t: dict[str, Any], decisions: list[str]) -> str:
    """The user prompt for one platform: the PRESS items, the query tree, the string, its ledger, the lint and string
    check findings, and the researcher's query decisions (no seed-paper passage)."""
    tree = json.dumps(query.model_dump(mode="json", exclude_none=True), indent=1, ensure_ascii=False)
    parts = ["PRESS items (use these keys):", *[f"- {k}: {v}" for k, v in PRESS_ITEMS.items()], "",
             f"Platform: {key} (syntax {t['style']})", "", "Query tree (the blocks, their fields and terms, and how "
             "they combine):", tree, "", "Search string:", t["string"], "",
             "What the platform's syntax loses or widens (translation ledger):"]
    parts += [f"- {r['kind']}: {r['text']}" for r in t["ledger"]] or ["- nothing lost"]
    parts += ["", "Linter findings on the query:"]
    parts += [f"- {f['level']} (block {f.get('block')}): {f['message']}: {f.get('term', '')}" for f in t["lint"]] or ["- none"]
    parts += ["", "String check findings:"]
    parts += [f"- {f['level']} at position {f['pos']}: {f['message']}" for f in t["check"]] or ["- none"]
    parts += ["", "The researcher's decisions about the query (deliberate; not problems):"]
    parts += [f"- {d}" for d in decisions] or ["- none recorded"]
    return "\n".join(parts)


# ------------------------------------------------------------------ matching
def _overlap(a: dict, b: dict) -> bool:
    qa, qb = norm(a["quote"]), norm(b["quote"])
    return a["item"] == b["item"] and bool(qa) and bool(qb) and (qa in qb or qb in qa)


def agree(run1: list[dict], run2: list[dict]) -> list[dict]:
    """The findings of both runs with their agreement: a run-1 finding matched by a run-2 finding (same item, one quote
    inside the other; each run-2 finding matched once) is "agreed" and listed once; every other finding is
    "unsure"."""
    used, out = set(), []
    for f in run1:
        j = next((j for j, g in enumerate(run2) if j not in used and _overlap(f, g)), None)
        if j is None:
            out.append({**f, "agreement": "unsure"})
        else:
            used.add(j)
            out.append({**f, "agreement": "agreed", "readers": [f["reader"], run2[j]["reader"]]})
    out += [{**g, "agreement": "unsure"} for j, g in enumerate(run2) if j not in used]
    for f in out:
        f.setdefault("readers", [f["reader"]])
    return out


# ------------------------------------------------------------------ the run
def _reserve(project) -> str:
    """A run folder of its own: two runs started in the same second get ``<stamp>`` and ``<stamp>-2``, so a run's
    record is never overwritten (risk check of 2c, R3)."""
    from ..core.provenance import stamp

    base = project.root / "runs" / RUN_KIND
    base.mkdir(parents=True, exist_ok=True)
    st, i = stamp(), 1
    while True:
        name = st if i == 1 else f"{st}-{i}"
        try:
            (base / name).mkdir()
            return name
        except FileExistsError:
            i += 1


def second_read(project, version: int | None = None, platform: str | None = None, readers: list[str] | None = None,
                make=None) -> dict[str, Any]:
    """Run the second reader on the strings of synonyms ``version`` (default: the accepted round, else the newest).
    ``readers`` names one or two backends for this run (default: the ``review`` stage, else the construct backend).
    ``make(cfg)`` returns a ready provider (tests); otherwise each reader needs its ``research`` consent
    (``BackendConsentRequired`` before anything is sent). A CLI agent without its tool-off flags raises
    :class:`ReaderRefused` before any consent or call."""
    import asyncio

    from ..construct import artifacts
    from ..core.provenance import sha256_text
    from ..core.usage import track
    from ..interview.ledger import prompt_texts
    from ..seeds.mine import CALLS_PER_ITEM, rules_request
    from .ir import Query
    from .rounds import default_version

    r = resolve_readers(project, readers)
    cfgs = r["configs"]
    for cfg in {c.name: c for c in cfgs}.values():   # the gate first: a refused reader is never asked for consent
        if why := _gate(cfg):
            raise ReaderRefused(why)
    row, data = artifacts.load(project, "synonyms", version if version is not None else default_version(project))
    if row is None:
        raise ValueError("no synonyms artifact yet; propose one or import a search string first")
    query = Query.model_validate(data)
    tg = targets(project, query, platform)
    tg = {k: t for k, t in tg.items() if t["style"] != "gscholar"} or tg   # the reduced Scholar string is one-way
    providers: dict[str, Any] = {}
    for cfg in cfgs:
        if cfg.name not in providers:
            if make is not None:
                providers[cfg.name] = make(cfg)
            else:
                from ..providers.registry import research_provider
                providers[cfg.name] = research_provider(project, cfg)
    decisions = prompt_texts(project)
    st = _reserve(project)
    run_ref = f"{RUN_KIND}/{st}"

    async def one(key, run, cfg):
        seed = (cfg.seed if cfg.seed is not None else 1) + run - 1   # the second run on one reader gets the next seed
        req = rules_request(SYSTEM, prompt(query, key, tg[key], decisions), SCHEMA, seed)
        return key, run, cfg, req, await providers[cfg.name].complete(req, retries=CALLS_PER_ITEM)

    async def run_all():
        return await asyncio.gather(*(one(k, run, cfg) for k in tg for run, cfg in enumerate(cfgs, 1)))

    with track(project, USAGE_STAGE, run_ref):
        results = asyncio.run(run_all())
    calls, kept = [], {k: {1: [], 2: []} for k in tg}
    dropped, failed = {k: 0 for k in tg}, 0
    for key, run, cfg, req, res in results:
        hay = norm(tg[key]["string"])
        rec = {"platform": key, "run": run, "reader": reader_label(cfg, run), "backend": cfg.name, "model": cfg.model,
               "input_sha256": res.input_sha256 or req.input_sha256,
               "output_sha256": sha256_text(res.text) if res.text else None, "attempts": res.attempts,
               "repairs": res.repairs, "ok": res.ok, "error": (res.error or "")[:200] or None,
               "temperature": req.temperature, "sampling_seed": req.seed, "kept": 0, "dropped": 0}
        if not res.ok:
            failed += 1
        else:
            for f in (res.parsed or {}).get("findings", []):
                q = norm(f.get("quote") or "")
                if not q or q not in hay or f.get("item") not in PRESS_ITEMS:
                    dropped[key] += 1
                    rec["dropped"] += 1
                    continue
                rec["kept"] += 1
                kept[key][run].append({"item": f["item"], "quote": f["quote"][:400], "note": (f.get("note") or "")[:800],
                                       "severity": f.get("severity") if f.get("severity") in SEVERITIES else "suggestion",
                                       "reader": reader_label(cfg, run), "backend": cfg.name})
        calls.append(rec)
    calls.sort(key=lambda c: (list(tg).index(c["platform"]), c["run"]))
    plats = {}
    for key, t in tg.items():
        plats[key] = {"style": t["style"], "string_sha256": sha256_text(t["string"]),
                      "findings": agree(kept[key][1], kept[key][2]), "dropped": dropped[key],
                      "failed": sum(not c["ok"] for c in calls if c["platform"] == key), "n_calls": len(cfgs)}
    n_agreed = sum(f["agreement"] == "agreed" for p in plats.values() for f in p["findings"])
    n_unsure = sum(f["agreement"] == "unsure" for p in plats.values() for f in p["findings"])
    n_dropped = sum(dropped.values())
    parts = [f"{n_agreed} agreed", f"{n_unsure} unsure"]
    if n_dropped:
        parts.append(f"{n_dropped} dropped: quote not found")
    if failed:
        parts.append(f"{failed} call(s) failed after {CALLS_PER_ITEM} calls at most")
    out = {"at": st, "run_ref": run_ref, "synonyms_version": row["version"],
           "readers": [{"run": i, "name": c.name, "model": c.model, "family": family_of(c),
                        "locality": getattr(providers[c.name], "locality", None) or _locality(c),
                        "label": reader_label(c, i)} for i, c in enumerate(cfgs, 1)],
           "source": r["source"], "notices": r["notices"], "platforms": plats, "agreed": n_agreed, "unsure": n_unsure,
           "dropped": n_dropped, "failed": failed, "message": "second reader: " + ", ".join(parts),
           "note": "a model second reader, not a human peer review; its findings change nothing"}
    with project.store.tx() as c:
        for key, runs in kept.items():   # one row per finding per run; agreement is recomputed on read
            for f in runs[1] + runs[2]:
                c.execute("INSERT INTO review_findings (version, platform, reader, item, quote, note, severity, at) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (row["version"], key, f["reader"], f["item"], f["quote"], f["note"], f["severity"], st))
    d = project.root / "runs" / RUN_KIND / st
    (d / "review.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "calls.json").write_text(json.dumps(calls, indent=2) + "\n", encoding="utf-8")
    out["run_dir"] = str(d.relative_to(project.root))
    return out


def _locality(cfg) -> str:
    from ..core.summary import provider_locality

    return provider_locality(cfg.kind, cfg.base_url)


def last_review(project) -> dict[str, Any] | None:
    """The newest run's ``review.json``, or None."""
    base = Path(project.root) / "runs" / RUN_KIND
    runs = sorted(p for p in base.glob("*/review.json")) if base.exists() else []
    if not runs:
        return None
    out = json.loads(runs[-1].read_text(encoding="utf-8"))
    out["run_dir"] = str(runs[-1].parent.relative_to(project.root))
    return out


def lines(review: dict[str, Any]) -> list[str]:
    """The review as the CLI prints it: readers, notices, then each platform's findings."""
    out = [review["message"], review["note"]]
    for rd in review["readers"]:
        out.append(f"reader {rd['run']}: {rd['name']} ({rd['model'] or 'no model'}, family {rd['family']}, "
                   f"{rd['locality'] or 'unknown'})")
    out += review["notices"]
    for key, pl in review["platforms"].items():
        out.append(f"# {key}")
        if not pl["findings"]:
            out.append(empty_text(pl))
        for f in pl["findings"]:
            tag = "agreed" if f["agreement"] == "agreed" else "unsure"
            out.append(f"[{tag}] {f['item']} ({f['severity']}, {', '.join(f['readers'])}): \"{f['quote']}\" - {f['note']}")
        if pl["dropped"]:
            out.append(f"{pl['dropped']} dropped: quote not found")
        if pl.get("failed") and pl["findings"]:
            out.append(f"{pl['failed']} call(s) failed: the findings come from the reader(s) that answered")
    return out


def empty_text(pl: dict[str, Any]) -> str:
    """What a platform without findings says: a failed call is not a reader that found nothing."""
    failed, n = pl.get("failed") or 0, pl.get("n_calls") or 0
    if failed and failed >= n:
        return "no answer: every call failed, so this is not a review"
    if failed:
        return f"no findings from the reader that answered; {failed} call(s) failed"
    return "no findings"
