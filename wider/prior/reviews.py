"""Prior reviews: add (file, PDF or pasted text), list, remove, policy switches. **Local only.**

Files are copied into ``<project>/prior_reviews/`` (gitignored, outside ``inputs/`` so the importer never
scans them) and parsed on this computer. Nothing here opens a network connection; the two opt-in switches
in ``config.prior_reviews`` gate ``chain.py`` (OpenAlex) and the excerpt block in construct proposals.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from ..construct.seed import parse_seed_text
from ..core.project import Project
from ..core.provenance import sha256_file, sha256_text, utcnow
from ..core.text import clean_doi, clean_title, norm_title, year_of
from ..ingest.readers import INPUT_SUFFIXES
from . import refs_pdf

PRIOR_SUFFIXES = INPUT_SUFFIXES | {".pdf", ".txt"}
DIR = "prior_reviews"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_BARE_DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?10\.\d{4,9}/\S+$", re.IGNORECASE)
_CSV_HEADER = re.compile(r"^[^\n]*\b(title|doi)\b[^\n]*[,;\t]", re.IGNORECASE)


class PolicyError(PermissionError):
    """Raised when an action would send prior-review data off this computer while the switch is off."""


def policy(project: Project) -> dict[str, Any]:
    return project.config.prior_reviews.model_dump(mode="json")


def require_lookup(project: Project) -> None:
    if not project.config.prior_reviews.allow_openalex_lookup:
        raise PolicyError("identifier lookups are off for this project: turn on 'allow OpenAlex lookup' in the setup "
                          "step 'prior reviews' to send DOIs and titles to OpenAlex")


def require_excerpts(project: Project) -> None:
    if not project.config.prior_reviews.allow_llm_excerpts:
        raise PolicyError("prior-review excerpts in LLM prompts are off for this project")


def set_policy(project: Project, **changes: Any) -> dict[str, Any]:
    """Update the switches (``none_known``, ``allow_openalex_lookup``, ``allow_llm_excerpts``,
    ``max_citing_per_paper``) and save ``project.yaml``."""
    pol = project.config.prior_reviews
    data = pol.model_dump()
    for k, v in changes.items():
        if v is None:
            continue
        if k not in data:
            raise ValueError(f"unknown policy field {k!r}")
        data[k] = v
    project.config.prior_reviews = type(pol)(**data)
    project.save()
    return policy(project)


# ----------------------------------------------------------------- folder
def ensure_dir(project: Project) -> Path:
    """``prior_reviews/`` with its own ``.gitignore`` (also for projects created before this folder existed)."""
    d = project.path(DIR)
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n!.gitignore\n", encoding="utf-8")
    return d


def safe_filename(name: str) -> str:
    base = Path(name or "review").name
    return _SAFE_NAME.sub("_", base).strip(" .") or "review"


# ----------------------------------------------------------------- parsing (all local)
def _row(title: str | None, doi: str | None, year: Any = None, first_author: Any = None, raw_ref: str | None = None,
         confidence: str | None = None) -> dict[str, Any]:
    doi = clean_doi(doi)
    title = clean_title(title or "")
    if isinstance(first_author, list):
        first_author = first_author[0] if first_author else None
    fa = str(first_author).split(",")[0].strip() if first_author else None
    conf = confidence or ("doi" if doi else "parsed")
    return {"title": title, "doi": doi, "year": year_of(year) if year is not None else None, "first_author": fa or None,
            "raw_ref": raw_ref, "confidence": conf}


def rows_from_export(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Reference-manager export (RIS, CSV, TSV, XLSX, XLS, BibTeX) read with the standard readers."""
    from ..ingest.readers import read_standard
    _meta, rows = read_standard(path, "prior_review")
    out = [_row(r.get("title"), r.get("doi"), r.get("year"), r.get("first_author") or r.get("authors")) for r in rows]
    return [r for r in out if r["title"] or r["doi"]], []


def rows_from_text(text: str, filename: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """Pasted text: RIS, CSV with title/doi columns, one DOI or title per line, or a reference list."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return [], []
    first = text.split("\n", 1)[0]
    if re.search(r"^TY  - ", text, flags=re.M) or (_CSV_HEADER.match(first) and "\n" in text):
        rows = [_row(r["title"], r["doi"], r.get("year"), r.get("first_author")) for r in parse_seed_text(text, filename)]
        return rows, []
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    rows: list[dict[str, Any]] = []
    for ln in lines:
        if _BARE_DOI.match(ln) and clean_doi(ln):
            rows.append(_row(None, ln, confidence="doi"))
        elif refs_pdf.YEAR_RE.search(ln) or refs_pdf.DOI_RE.search(ln):
            rows.append(refs_pdf.parse_entry(ln))  # a reference string
        else:
            rows.append(_row(ln, None, confidence="parsed"))  # a bare title
    return rows, []


def rows_from_file(path: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    """``(kind, rows, warnings)`` for a file in ``PRIOR_SUFFIXES``."""
    suf = path.suffix.lower()
    if suf == ".pdf":
        rows, warnings = refs_pdf.parse_pdf(path)
        return "pdf", rows, warnings
    if suf == ".txt":
        rows, warnings = rows_from_text(path.read_text(encoding="utf-8-sig", errors="replace"), path.name)
        return "text", rows, warnings
    if suf in INPUT_SUFFIXES:
        rows, warnings = rows_from_export(path)
        return "list", rows, warnings
    raise ValueError(f"unsupported file type {suf!r}; use one of {', '.join(sorted(PRIOR_SUFFIXES))}")


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Within one review: first by DOI, then by normalised title (rule of the seed-paper importer)."""
    seen_doi: set[str] = set()
    seen_title: set[str] = set()
    out = []
    for r in rows:
        doi, tn = r.get("doi"), norm_title(r.get("title") or "")
        if doi:
            if doi in seen_doi:
                continue
            seen_doi.add(doi)
        elif tn:
            if tn in seen_title:
                continue
            seen_title.add(tn)
        else:
            continue
        if tn:
            seen_title.add(tn)
        out.append(r)
    return out


# ----------------------------------------------------------------- store
def _insert(project: Project, label: str, kind: str, file_name: str | None, sha: str | None,
            rows: list[dict[str, Any]], note: str | None) -> dict[str, Any]:
    st = project.store
    if sha and (dup := st.one("SELECT review_id, label FROM prior_reviews WHERE sha256=?", (sha,))):
        what = "this text" if kind == "text" else "this file"
        raise ValueError(f"{what} was already added as review #{dup['review_id']} ({dup['label']})")
    rows = dedupe_rows(rows)
    now = utcnow()
    with st.tx():
        cur = st.execute("INSERT INTO prior_reviews(label, kind, file_name, sha256, n_papers, added_at, note) "
                         "VALUES (?,?,?,?,?,?,?)", (label, kind, file_name, sha, len(rows), now, note))
        rid = cur.lastrowid
        st.executemany(
            "INSERT INTO prior_review_papers(review_id, position, title, title_norm, doi, doi_norm, year, first_author, "
            "raw_ref, confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(rid, i + 1, r["title"] or None, norm_title(r["title"]) or None, r["doi"], r["doi"], r["year"],
              r["first_author"], r.get("raw_ref"), r["confidence"]) for i, r in enumerate(rows)])
    return get(project, rid)


def add_file(project: Project, src: str | Path, label: str | None = None, note: str | None = None) -> dict[str, Any]:
    """Copy a file into ``prior_reviews/``, parse it locally and store its papers. Refuses the same file twice."""
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    if src.suffix.lower() not in PRIOR_SUFFIXES:
        raise ValueError(f"unsupported file type {src.suffix!r}; use one of {', '.join(sorted(PRIOR_SUFFIXES))}")
    sha = sha256_file(src)
    if dup := project.store.one("SELECT review_id, label FROM prior_reviews WHERE sha256=?", (sha,)):
        raise ValueError(f"this file was already added as review #{dup['review_id']} ({dup['label']})")
    d = ensure_dir(project)
    dst = d / safe_filename(src.name)
    if dst.exists() and sha256_file(dst) != sha:
        dst = d / f"{dst.stem}_{sha[:8]}{dst.suffix}"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    kind, rows, warnings = rows_from_file(dst)
    res = _insert(project, label or src.stem, kind, dst.name, sha, rows,
                  note or ("; ".join(warnings) if warnings else None))
    res["warnings"] = warnings
    return res


def add_text(project: Project, text: str, label: str, filename: str = "", note: str | None = None) -> dict[str, Any]:
    """Pasted DOIs, titles, a reference list, RIS or CSV text."""
    rows, warnings = rows_from_text(text, filename)
    if not rows:
        raise ValueError("no DOI, title or reference found in the pasted text")
    res = _insert(project, label or "pasted list", "text", None, sha256_text(text), rows,
                  note or ("; ".join(warnings) if warnings else None))
    res["warnings"] = warnings
    return res


def remove(project: Project, review_id: int) -> None:
    st = project.store
    row = st.one("SELECT file_name FROM prior_reviews WHERE review_id=?", (review_id,))
    if not row:
        raise KeyError(f"no prior review #{review_id}")
    with st.tx():
        st.execute("DELETE FROM prior_reviews WHERE review_id=?", (review_id,))
    if row["file_name"] and not st.one("SELECT 1 FROM prior_reviews WHERE file_name=?", (row["file_name"],)):
        (project.path(DIR) / row["file_name"]).unlink(missing_ok=True)


def get(project: Project, review_id: int) -> dict[str, Any]:
    rows = [r for r in list_reviews(project) if r["review_id"] == review_id]
    if not rows:
        raise KeyError(f"no prior review #{review_id}")
    return rows[0]


def list_reviews(project: Project) -> list[dict[str, Any]]:
    """One row per review with paper counts (``n_papers``, ``n_doi``, ``n_resolved``, ``n_matched``)."""
    st = project.store
    rows = st.query(
        "SELECT r.review_id, r.label, r.kind, r.file_name, r.sha256, r.n_papers, r.added_at, r.note, "
        "SUM(p.doi_norm IS NOT NULL) AS n_doi, SUM(p.openalex_id IS NOT NULL) AS n_resolved, "
        "SUM(EXISTS(SELECT 1 FROM prior_review_matches m WHERE m.paper_id=p.paper_id AND m.record_ref IS NOT NULL)) AS n_matched "
        "FROM prior_reviews r LEFT JOIN prior_review_papers p ON p.review_id=r.review_id "
        "GROUP BY r.review_id ORDER BY r.review_id")
    return [{**dict(r), "n_doi": int(r["n_doi"] or 0), "n_resolved": int(r["n_resolved"] or 0),
             "n_matched": int(r["n_matched"] or 0)} for r in rows]


def papers(project: Project, review_id: int | None = None) -> list[dict[str, Any]]:
    """Papers of one review (or of all reviews), in list order."""
    st = project.store
    sql = ("SELECT p.*, r.label AS review_label FROM prior_review_papers p JOIN prior_reviews r ON r.review_id=p.review_id ")
    if review_id is None:
        return [dict(r) for r in st.query(sql + "ORDER BY p.review_id, p.position")]
    return [dict(r) for r in st.query(sql + "WHERE p.review_id=? ORDER BY p.position", (review_id,))]


def summary(project: Project) -> dict[str, Any]:
    """``{n_reviews, n_papers, n_doi, policy}`` for the setup interview and ``wider status``."""
    st = project.store
    n_reviews = int(st.scalar("SELECT COUNT(*) FROM prior_reviews") or 0)
    n_papers = int(st.scalar("SELECT COUNT(*) FROM prior_review_papers") or 0)
    n_doi = int(st.scalar("SELECT COUNT(*) FROM prior_review_papers WHERE doi_norm IS NOT NULL") or 0)
    return {"n_reviews": n_reviews, "n_papers": n_papers, "n_doi": n_doi, "policy": policy(project)}
