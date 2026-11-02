"""Reference lists of the Development seed papers (papers beyond the query, D26): OpenAlex first (under the DOI
consent, purpose ``seed_refs``), the seed's PDF as the fallback, and the entries one route has and the other lacks.
Optional tiers parse PDF entries further, each chosen by the researcher: a GROBID server on this computer, a Crossref
match by title only (purpose ``ref_match``), or a model that structures an entry and keeps only what the entry says.

Validation seeds are never processed: their DOIs are in no request and their reference lists are not read. A
reference DOI that OpenAlex did not list becomes a ``references`` candidate with origin ``pdf``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.project import Project
from ..core.provenance import utcnow
from ..core.text import clean_doi, norm_text, norm_title

ROUTES = ("auto", "openalex", "pdf")
TIERS = ("grobid", "crossref", "model")
VALIDATION_RULE = ("{id} is a validation paper: a validation paper's reference list is not mined, sent or counted; "
                   "move it to Development to use it")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
GROBID_REFUSED = ("GROBID must run on this computer (127.0.0.1, localhost or ::1): it is sent the whole PDF, and the "
                  "privacy rule lets no seed PDF leave the computer")
GROBID_UNSET = "no GROBID server is set: wider seeds policy grobid-url http://127.0.0.1:8070 (one you run yourself)"
# The processReferences service, consolidation off; endpoint and parameter names pending the author's check (task 1.2c)
GROBID_PATH = "/api/processReferences"
GROBID_PARAMS = {"consolidateCitations": "0", "includeRawCitations": "1"}
# Crossref's bibliographic search; the field names are pending the author's check (task 1.2b)
CROSSREF_WORKS = "https://api.crossref.org/works"
CROSSREF_ROWS = 5
FUZZY = "fuzzy (confirm)"
OFFLINE = "online lookups are off: only the PDF route ran and nothing was sent"
TEMPLATE = Path(__file__).parent / "templates" / "reference_entry.md"
SCHEMA = {"type": "object", "properties": {"authors": {"type": "string"}, "title": {"type": "string"},
                                           "year": {"type": "string"}, "venue": {"type": "string"},
                                           "doi": {"type": "string"}}}
FIELDS = ("authors", "title", "year", "venue", "doi")
USAGE_STAGE = "ref_structure"   # the llm_usage stage of the model tier; backend: construct


def check_grobid_url(url: str) -> str:
    """The URL when it names a loopback host over http(s); ``ValueError`` naming the privacy rule otherwise."""
    u = urlsplit((url or "").strip())
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK:
        raise ValueError(GROBID_REFUSED)
    return url.strip().rstrip("/")


def _seeds(project: Project, benchmark_id: str | None):
    papers = project.read_benchmark()
    if benchmark_id is None:
        return [p for p in papers if p.set == "Development"], sum(p.set == "Validation" for p in papers)
    hit = [p for p in papers if p.benchmark_id == benchmark_id]
    if not hit:
        raise KeyError(f"no seed paper {benchmark_id}")
    if hit[0].set != "Development":
        raise ValueError(VALIDATION_RULE.format(id=benchmark_id))
    return hit, 0


def _stored(project: Project, benchmark_id: str, route: str) -> list[dict]:
    return [dict(r) for r in project.store.query("SELECT * FROM seed_references WHERE benchmark_id=? AND route=? "
                                                 "ORDER BY ref_index", (benchmark_id, route))]


def store_openalex(project: Project, benchmark_id: str, rows: list[dict]) -> None:
    """Keep the works a seed cites as its OpenAlex list (also called by a backward chaining run)."""
    now = utcnow()
    with project.store.tx() as c:
        c.execute("DELETE FROM seed_references WHERE benchmark_id=? AND route='openalex'", (benchmark_id,))
        for i, w in enumerate(rows):
            c.execute("INSERT INTO seed_references(benchmark_id, ref_index, openalex_id, doi, title, year, venue, route, "
                      "at) VALUES (?,?,?,?,?,?,?,?,?)", (benchmark_id, i, w.get("openalex_id"), clean_doi(w.get("doi")),
                                                        w.get("title") or "", w.get("year"), w.get("journal") or None,
                                                        "openalex", now))


def _store_pdf(project: Project, benchmark_id: str, entries: list[dict]) -> None:
    now = utcnow()
    with project.store.tx() as c:
        c.execute("DELETE FROM seed_references WHERE benchmark_id=? AND route='pdf'", (benchmark_id,))
        for i, e in enumerate(entries):
            c.execute("INSERT INTO seed_references(benchmark_id, ref_index, raw_text, doi, title, year, venue, route, "
                      "tier, matched_by, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (benchmark_id, i, e.get("raw_ref") or "", e.get("doi"), e.get("title") or "", e.get("year"),
                       e.get("venue"), "pdf", e.get("tier") or "plain",
                       "; ".join(x for x in (e.get("matched_by"), e.get("match")) if x) or None, now))


# ------------------------------------------------------------------------------------------------ routes
def _openalex(project: Project, seeds, client) -> dict[str, dict]:
    """Per seed: ``{rows, source, failed, message}``. A list already stored (by an earlier run or a backward chaining
    run) is reused without a request; the others are asked in one ``seed_refs`` run."""
    from ..core.lookups import lookup_client
    from ..prior.chain import _fetch_related
    from .chain import _resolve

    out: dict[str, dict] = {}
    todo = []
    for p in seeds:
        rows = _stored(project, p.benchmark_id, "openalex")
        if rows:
            out[p.benchmark_id] = {"rows": rows, "source": "stored", "failed": False, "message": ""}
        elif not clean_doi(p.doi):
            out[p.benchmark_id] = {"rows": [], "source": None, "failed": False,
                                   "message": "no DOI, so OpenAlex was not asked; resolve the seed first"}
        else:
            todo.append(p)
    if not todo:
        return out
    with lookup_client(project, "seed_refs", client) as c:   # consent first: nothing is sent without it
        ids = _resolve(c, [clean_doi(p.doi) for p in todo])
        resolve_failed = bool(c.failed.get("openalex"))
        for p in todo:
            oid = ids.get(clean_doi(p.doi))
            if not oid:
                out[p.benchmark_id] = {"rows": [], "source": None, "failed": resolve_failed,
                                       "message": "the OpenAlex request failed" if resolve_failed
                                       else "not found on OpenAlex"}
                continue
            before = c.failed.get("openalex", 0)
            rows, _capped = _fetch_related(c, oid, "backward", None)
            if not rows and c.failed.get("openalex", 0) > before:
                out[p.benchmark_id] = {"rows": [], "source": None, "failed": True,
                                       "message": "the OpenAlex request failed"}
                continue
            store_openalex(project, p.benchmark_id, rows)
            out[p.benchmark_id] = {"rows": _stored(project, p.benchmark_id, "openalex"), "source": "request",
                                   "failed": False, "message": ""}
    return out


def _pdf_entries(project: Project, benchmark_id: str, pdf_path: str | Path | None) -> tuple[list[dict], str, str]:
    """``(entries, source, message)``: the parsed entries of the PDF given, else of the stored seed PDF (extracted
    first when it was not). Nothing is sent."""
    from ..prior import refs_pdf
    from . import pdf

    if pdf_path is not None:
        rows, warnings = refs_pdf.parse_pdf(pdf_path)
        return rows, "file", "; ".join(warnings)
    if benchmark_id not in pdf.with_pdf(project):
        return [], "", "no PDF, so the PDF route did not run"
    t = pdf.load_text(project, benchmark_id)
    if t is None:
        try:
            pdf.extract(project, benchmark_id)
        except (ImportError, ValueError) as e:
            return [], "", str(e) if isinstance(e, ValueError) else refs_pdf.PDF_EXTRA
        t = pdf.load_text(project, benchmark_id) or {"references": []}
    return list(t["references"]), "stored", ""


def _pdf_file(project: Project, benchmark_id: str, pdf_path) -> Path | None:
    from . import pdf

    if pdf_path is not None:
        return Path(pdf_path)
    return pdf.file_path(project, benchmark_id) if benchmark_id in pdf.with_pdf(project) else None


# ------------------------------------------------------------------------------------------------ tiers
def _tei_entries(xml: str) -> list[dict]:
    """Entries from GROBID's TEI answer: one per ``biblStruct``, its raw citation kept as the entry text."""
    import xml.etree.ElementTree as ET

    ns = {"t": "http://www.tei-c.org/ns/1.0"}
    root = ET.fromstring(xml)
    out = []
    for b in root.iter("{http://www.tei-c.org/ns/1.0}biblStruct"):
        raw = b.find(".//t:note[@type='raw_reference']", ns)
        title = b.find("t:analytic/t:title", ns)
        if title is None:
            title = b.find("t:monogr/t:title", ns)
        doi = b.find(".//t:idno[@type='DOI']", ns)
        date = b.find(".//t:date[@when]", ns)
        venue = b.find("t:monogr/t:title", ns) if b.find("t:analytic", ns) is not None else None
        text = "".join(raw.itertext()) if raw is not None else " ".join("".join(b.itertext()).split())
        year = re.match(r"\d{4}", date.get("when", "")) if date is not None else None
        out.append({"raw_ref": " ".join(text.split()), "title": " ".join("".join(title.itertext()).split())
                    if title is not None else "", "doi": clean_doi(doi.text) if doi is not None else None,
                    "year": int(year.group(0)) if year else None,
                    "venue": " ".join("".join(venue.itertext()).split()) if venue is not None else None,
                    "tier": "grobid"})
    return out


def _grobid(project: Project, path: Path | None, http=None) -> tuple[list[dict] | None, str]:
    """``(entries, message)``; ``entries`` is ``None`` when GROBID gave nothing (the plain entries stay)."""
    import httpx

    url = project.config.seed_pdfs.grobid_url
    if not url:
        raise ValueError(GROBID_UNSET)
    url = check_grobid_url(url)   # before anything is read or sent
    if path is None:
        return None, "GROBID needs the seed's PDF; none is kept"
    try:
        with path.open("rb") as fh:
            if http is None:   # no proxy from the environment or the system: the PDF goes to loopback only
                with httpx.Client(trust_env=False, timeout=120) as c:
                    r = c.post(url + GROBID_PATH, files={"input": (path.name, fh, "application/pdf")},
                               data=GROBID_PARAMS)
            else:
                r = http.post(url + GROBID_PATH, files={"input": (path.name, fh, "application/pdf")},
                              data=GROBID_PARAMS, timeout=120)
        r.raise_for_status()
        entries = _tei_entries(r.text)
    except (httpx.HTTPError, OSError):
        return None, f"GROBID not reachable at {url}; the plain PDF entries are kept"
    except Exception:   # an answer that is not TEI
        return None, f"GROBID at {url} gave no reference list; the plain PDF entries are kept"
    return entries, f"GROBID parsed {len(entries)} entries"


def _crossref(project: Project, entries: list[dict], client) -> str:
    """Match each entry without a DOI by its parsed title only; the year filters the answers here, and a DOI is kept
    only above the Jaro-Winkler threshold, marked "fuzzy (confirm)". An entry parsed only heuristically (no year, so
    its "title" may be the author list or the whole entry) is not sent and counted."""
    from ..core.lookups import _mailto, lookup_client
    from ..core.matching import jaro_winkler

    todo = [e for e in entries if not e.get("doi") and (e.get("title") or "").strip()]
    # a heuristic parse found no title of its own (it may be the author list or the whole entry): never sent
    vague = [e for e in todo if e.get("confidence") == "heuristic" and e.get("tier") != "model"]
    todo = [e for e in todo if e not in vague]
    held = (f"; {len(vague)} entr{'y' if len(vague) == 1 else 'ies'} whose title could not be told apart from the "
            "rest of the entry not sent") if vague else ""
    if not todo:
        return f"Crossref: every entry has a DOI or no title, nothing was sent{held}"
    thr = project.config.thresholds.jw_benchmark
    found = 0
    with lookup_client(project, "ref_match", client) as c:
        for e in todo:
            params = {"query.bibliographic": e["title"], "rows": CROSSREF_ROWS}
            if _mailto():
                params["mailto"] = _mailto()
            try:
                r = c.get(CROSSREF_WORKS, params=params)
                items = (r.json().get("message") or {}).get("items") or [] if r.status_code == 200 else []
            except Exception:
                items = []
            best, score = None, 0.0
            for it in items:
                year = (((it.get("issued") or {}).get("date-parts") or [[None]])[0] or [None])[0]
                if e.get("year") and year and abs(int(year) - int(e["year"])) > 1:
                    continue
                t = (it.get("title") or [""])[0]
                s = jaro_winkler(norm_title(t), norm_title(e["title"]))
                if s >= thr and s > score:
                    best, score = it, s
            if best is not None and clean_doi(best.get("DOI")):
                e.update(doi=clean_doi(best["DOI"]), tier="crossref", match=FUZZY)
                found += 1
    return f"Crossref: {found} of {len(todo)} entries without a DOI matched by title, each {FUZZY}{held}"


def _model(project: Project, entries: list[dict], provider=None) -> dict[str, Any]:
    """Structure each raw entry; a field is kept only when the raw entry holds it. The entry goes to a backend that is
    not local only under ``seed_pdfs.passages_to_cloud`` and that backend's ``passages`` consent."""
    import asyncio

    from ..core import backend_consent as bc
    from ..core.provenance import sha256_text
    from ..core.usage import track
    from . import mine

    if not entries:
        return {"ran": False, "message": "the model tier had no entry to read"}
    try:
        cfg, loc = mine.target(project, provider)
    except (KeyError, TypeError):
        return {"ran": False, "message": "no backend for the stage construct, so the model pass did not run: "
                                          "the entries stay as parsed"}
    why = mine.model_gate(project, cfg, loc)
    if why:
        return {"ran": False, "message": why.replace("seed-paper passage", "reference entry")
                .replace("gets no passage", "gets no reference entry")}   # the setting's own name stays as it is
    if provider is None:
        from ..providers.registry import research_provider
        provider = research_provider(project, cfg)
    bc.require_backend_consent(project, cfg, "research")
    bc.require_backend_consent(project, cfg, "passages")
    system = TEMPLATE.read_text(encoding="utf-8")
    seed = cfg.seed if cfg.seed is not None else 1
    reqs = [mine.rules_request(system, f"Reference entry:\n{e.get('raw_ref') or ''}", SCHEMA, seed) for e in entries]

    async def run_all():
        return await asyncio.gather(*(provider.complete(r, retries=mine.CALLS_PER_ITEM) for r in reqs))

    with track(project, USAGE_STAGE):
        results = asyncio.run(run_all())
    calls, kept, dropped, failed = [], 0, 0, 0
    for e, req, res in zip(entries, reqs, results):
        rec = {"input_sha256": res.input_sha256 or req.input_sha256,
               "output_sha256": sha256_text(res.text) if res.text else None, "attempts": res.attempts,
               "repairs": res.repairs, "ok": res.ok, "kept": [], "dropped": []}
        calls.append(rec)
        if not res.ok:
            failed += 1
            continue
        hay = norm_text(e.get("raw_ref") or "")
        got = {}
        for f in FIELDS:
            v = str((res.parsed or {}).get(f) or "").strip()
            if not v:
                continue
            if norm_text(v) and f" {norm_text(v)} " in f" {hay} ":   # whole words of the entry only
                got[f] = v
                rec["kept"].append(f)
            else:
                rec["dropped"].append(f)
                dropped += 1
        kept += len(got)
        if got.get("title"):
            e["title"] = got["title"]
        if got.get("doi") and clean_doi(got["doi"]):
            e["doi"] = clean_doi(got["doi"])
        if got.get("year") and re.fullmatch(r"\d{4}", got["year"]):
            e["year"] = int(got["year"])
        if got.get("venue"):
            e["venue"] = got["venue"]
        if got:
            e["tier"] = "model"
    parts = [f"{kept} field(s) kept"]
    if dropped:
        parts.append(f"{dropped} dropped: not in the entry")
    if failed:
        parts.append(f"{failed} entr{'ies' if failed != 1 else 'y'} failed after {mine.CALLS_PER_ITEM} calls at most")
    return {"ran": True, "message": "model tier: " + ", ".join(parts), "kept": kept, "dropped": dropped,
            "failed": failed, "calls": calls, "provider": provider.identity(), "backend_kind": cfg.kind}


def needs_consent(project: Project, tier: str | None, provider=None) -> list[tuple[Any, str]]:
    """The backend consents the model tier still lacks (``research``, then ``passages``); empty for other tiers."""
    from . import mine

    return mine.needs_consent(project, provider) if tier == "model" else []


# ------------------------------------------------------------------------------------------------ diff and candidates
def _match(e: dict, oa: list[dict], thr: float) -> str | None:
    from ..core.matching import jaro_winkler

    if e.get("doi") and any(r["doi"] == e["doi"] for r in oa):
        return "doi"
    t = norm_title(e.get("title"))
    if t and any(norm_title(r["title"]) == t for r in oa):
        return "title"
    if t and any(jaro_winkler(norm_title(r["title"]), t) >= thr for r in oa if r["title"]):
        return "jaro_winkler"
    return None


def _candidates(project: Project, benchmark_id: str, entries: list[dict]) -> int:
    """Each DOI of an entry OpenAlex did not list becomes a ``references`` candidate with origin ``pdf`` (a known
    candidate gains the origin and the seed). A DOI only Crossref matched stays out: it is fuzzy until confirmed."""
    from .chain import _generations, _origins

    papers = project.read_benchmark()
    known = {clean_doi(p.doi) for p in papers if clean_doi(p.doi)}
    rnd = 1 + _generations(project, papers).get(benchmark_id, 0)
    now = utcnow()
    n_new = 0
    with project.store.tx() as c:
        for e in entries:
            doi = e.get("doi")
            if not doi or doi in known or e.get("matched_by") or e.get("tier") == "crossref":
                continue
            row = c.execute("SELECT * FROM seed_chain_candidates WHERE doi=?", (doi,)).fetchone()
            if row is None:
                n_new += 1
                c.execute("INSERT INTO seed_chain_candidates(doi, title, title_norm, year, direction, "
                          "linking_seeds_json, n_linking, status, fetched_at, origins_json, chain_round) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (doi, e.get("title") or "", norm_title(e.get("title")) or None, e.get("year"), "references",
                           json.dumps([benchmark_id]), 1, "new", now, json.dumps(["pdf"]), rnd))
                continue
            seeds = json.loads(row["linking_seeds_json"] or "[]")
            seeds += [] if benchmark_id in seeds else [benchmark_id]
            origins = _origins(row) + ([] if "pdf" in _origins(row) else ["pdf"])
            c.execute("UPDATE seed_chain_candidates SET linking_seeds_json=?, n_linking=?, origins_json=? "
                      "WHERE candidate_id=?", (json.dumps(seeds), len(seeds), json.dumps(origins), row["candidate_id"]))
    return n_new


def seed_references(project: Project, benchmark_id: str | None = None, route: str = "auto",
                    pdf_path: str | Path | None = None, tier: str | None = None, client: Any = None,
                    http: Any = None, provider: Any = None) -> dict[str, Any]:
    """Fill the reference lists of the Development seeds (or the one named). ``auto`` asks OpenAlex when online
    lookups are allowed and reads the PDF in any case; ``openalex`` needs the consent (``ConsentRequired`` before
    anything is sent); ``pdf`` sends nothing. ``ValueError`` for a validation seed, a bad route or tier, a refused
    GROBID URL; ``KeyError`` for an unknown seed."""
    from ..core import lookups

    if route not in ROUTES:
        raise ValueError("route must be auto, openalex or pdf")
    if tier is not None and tier not in TIERS:
        raise ValueError("tier must be grobid, crossref or model")
    if pdf_path is not None and benchmark_id is None:
        raise ValueError("--pdf needs the seed's id")
    if pdf_path is not None and route == "openalex":
        raise ValueError("--pdf reads a PDF: use the route auto or pdf")
    if tier == "grobid":   # refused before anything is read or sent
        if not project.config.seed_pdfs.grobid_url:
            raise ValueError(GROBID_UNSET)
        check_grobid_url(project.config.seed_pdfs.grobid_url)
    if tier == "crossref":
        lookups.require_consent(project, "ref_match")
    seeds, n_val = _seeds(project, benchmark_id)
    messages: list[str] = []
    oa: dict[str, dict] = {}
    if route == "openalex" or (route == "auto" and lookups.allowed(project)):
        oa = _openalex(project, seeds, client)
    elif route == "auto":
        messages.append(OFFLINE)
    thr = project.config.thresholds.jw_benchmark
    model_run = None
    out = []
    for p in seeds:
        b = p.benchmark_id
        o = oa.get(b)
        msgs = [o["message"]] if o and o["message"] else []
        entries, src = [], ""
        if route != "openalex":
            entries, src, m = _pdf_entries(project, b, pdf_path)
            entries = [{**e, "tier": "plain"} for e in entries]
            if m:
                msgs.append(m)
            if tier == "grobid" and src:
                g, m = _grobid(project, _pdf_file(project, b, pdf_path), http)
                msgs.append(m)
                if g is not None:
                    entries = g
            elif tier == "crossref" and entries:
                msgs.append(_crossref(project, entries, client))
            elif tier == "model" and entries:
                model_run = _model(project, entries, provider)
                msgs.append(model_run["message"])
        oa_rows = o["rows"] if o else []
        for e in entries:
            e["matched_by"] = _match(e, oa_rows, thr) if oa_rows else None
        lacking = [e for e in entries if oa_rows and not e["matched_by"]]
        if src:
            _store_pdf(project, b, entries)
        new = _candidates(project, b, entries) if src else 0
        parts = []
        if o is not None:
            parts.append("OpenAlex request failed" if o["failed"] else f"OpenAlex {len(oa_rows)} references")
        if src:
            parts.append(f"PDF {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
        out.append({"benchmark_id": b, "line": ", ".join(parts) or "no reference list",
                    "openalex": None if o is None else {"n": len(oa_rows), "source": o["source"], "failed": o["failed"]},
                    "pdf": {"n": len(entries), "source": src} if src else None,
                    "lacking": [{k: e.get(k) for k in ("raw_ref", "title", "doi", "year", "tier", "match")}
                                for e in lacking],
                    "new_candidates": new, "messages": msgs})
    if n_val:
        messages.append(f"{n_val} Validation seed{'s' if n_val != 1 else ''} not processed: a validation paper's "
                        "reference list is not mined, sent or counted")
    return {"route": route, "tier": tier, "seeds": out, "validation_not_processed": n_val, "messages": messages,
            "model": model_run}


def lines(d: dict[str, Any]) -> list[str]:
    """The CLI's text (and the panel's ``lines``)."""
    out = list(d["messages"])
    for s in d["seeds"]:
        out.append(f"{s['benchmark_id']}: {s['line']}")
        out += [f"  {m}" for m in s["messages"]]
        if s["lacking"]:
            out.append(f"  {len(s['lacking'])} entr{'ies' if len(s['lacking']) != 1 else 'y'} the PDF has and "
                       "OpenAlex lacks:")
            for e in s["lacking"]:
                tail = f" (doi {e['doi']}{', ' + e['match'] if e.get('match') else ''})" if e["doi"] else ""
                out.append(f"    {(e['raw_ref'] or e['title'] or '')[:200]}{tail}")
        if s["new_candidates"]:
            out.append(f"  {s['new_candidates']} new candidate(s) with origin pdf (wider benchmark candidates)")
    return out
