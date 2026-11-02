"""Review packages (reviewer-roles-and-blinding): one self-contained HTML page per reviewer that holds the records of
development or validation samples and the criteria, and no other judgment, so a second reviewer labels them blind on
any computer; the reviewer's exported decisions come back with :func:`import_file`.

Blinding holds by construction: the file carries an opaque token per item, the record's bibliographic fields, the
abstract the model judged and the criteria the first reviewer used, never a sample name, record id, score, decision,
note, tag or model output (D3). A package made stays on record (it may already have been sent); withdraw it instead.
WIDER never sends the file anywhere: it writes it into the project folder and to the path the researcher chose.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import secrets
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.project import Project
from ..core.provenance import utcnow
from . import labels as lb

MODES = ("second", "independent")
EARLIER = ("retain", "re-review", "exclude")
KIND_WORD = {"purposive": "development", "probability": "validation"}
FOLDER = "review-packages"
TEXTS = {
    "reviewers": "One reviewer: the results rest on one person's labels, and their mistakes are not caught. Two "
                 "independent reviewers: a second, blinded set of labels on the same records shows where the first may "
                 "be wrong, and the disagreements are resolved before the results. A second reviewer is recommended "
                 "for the validation includes and maybes, never required.",
    "limits": "A review package keeps other judgments out of the reviewer's view because it holds none. It does not "
              "prove who labelled: WIDER checks that a decisions file matches its package, not who made it.",
}
ONE_REVIEWER = "No independent second review was done; one reviewer labelled the samples."
NOT_FROZEN = "These criteria are not frozen yet."
PERMANENT = "A review package made stays on record, because the file may already have been sent; withdraw it instead."
FOREIGN = "This decisions file is not from a package of this project."
REPEAT_LIMIT = "a repeat review does not restore independence when the reviewer has seen earlier judgments"


class PackageError(ValueError):
    """A refusal with the HTTP status its route answers (400 bad input, 404 unknown, 409 a state that refuses)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------------------------------------ small helpers
def project_id(project: Project) -> str:
    """The project's random id, created on first use (a new store never runs ``_migrate_v9``); a decisions file
    carries it, so a file from another project's package is refused."""
    pid = project.store.get_meta("project_id")
    if not pid:
        pid = secrets.token_hex(8)
        project.store.set_meta("project_id", pid)
    return pid


def slug(name: str) -> str:
    """A reviewer name made safe for a file name: letters, digits, ``-`` and ``_``."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name or "").strip("-") or "reviewer"


def leaving_text(n: int, version: int | None, reviewer: str) -> str:
    """The sentence said once before a package is written (D4), the same for the CLI and the dialog."""
    return (f"This file leaves your computer when you send it. It holds the titles, abstracts, authors, years, journals "
            f"and DOIs of {n} record{'s' if n != 1 else ''}, the research questions, construct definitions and criteria "
            f"(codebook version {version}), the project title and the reviewer name {reviewer}. It holds no decision, "
            f"note, tag, model output or sample name.")


def earlier_meanings(reviewer: str) -> str:
    """What each earlier-labels choice means (D7), said once."""
    return (f"Retain: they count as they are. Re-review: {reviewer} labels those records again in the package, marked a "
            f"repeat; this does not restore independence if {reviewer} has seen other judgments. Exclude: they are left "
            f"out of reviewer agreement and the blinded-review counts, and stay in final labels and recall.")


def _date(at: str | None) -> str:
    if not at:
        return ""
    try:
        d = datetime.fromisoformat(at)
    except ValueError:
        return at[:10]
    return f"{d.day} {d.strftime('%b')}"


def _and(items: list) -> str:
    items = [str(x) for x in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _row(project: Project, package_id: str) -> dict:
    r = project.store.one("SELECT * FROM review_packages WHERE package_id=?", (package_id or "",))
    if r is None:
        raise KeyError(f"no review package {package_id}")
    d = dict(r)
    d["samples"] = json.loads(d["samples"] or "[]")
    d["excluded_label_ids"] = json.loads(d["excluded_label_ids"] or "[]")
    return d


def packages(project: Project) -> list[dict]:
    return [_row(project, r["package_id"]) for r in project.store.query(
        "SELECT package_id FROM review_packages ORDER BY made_at, package_id")]


def items(project: Project, package_id: str) -> list[dict]:
    return [dict(r) for r in project.store.query("SELECT * FROM review_package_items WHERE package_id=? ORDER BY rowid",
                                                 (package_id,))]


def _in_sample(project: Project, sample_id: int, record_id: int) -> bool:
    return bool(project.store.one("SELECT 1 FROM sample_rows WHERE sample_id=? AND record_id=?", (sample_id, record_id)))


def _decided(project: Project, pkg: dict, it: dict) -> bool:
    """An item the imports decided: the reviewer's label made in this package, or (not a repeat) any label of theirs;
    an item whose record left the sample counts as decided (D2)."""
    if not _in_sample(project, it["sample_id"], it["record_id"]):
        return True
    r = project.store.one("SELECT made_in FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                          (it["sample_id"], it["record_id"], pkg["reviewer"]))
    return r is not None and (r["made_in"] == pkg["package_id"] or not it["repeat"])


def is_open(project: Project, pkg: dict) -> bool:
    """Not withdrawn and with an item still undecided by the imports (a partly imported package stays open)."""
    return not pkg["withdrawn_at"] and any(not _decided(project, pkg, it) for it in items(project, pkg["package_id"]))


def exclude_applies(pkg: dict) -> bool:
    """The package's ``exclude`` choice applies while it is not withdrawn, or after a withdraw that followed an
    import (D6)."""
    return pkg["earlier"] == "exclude" and (not pkg["withdrawn_at"] or bool(pkg["imported_at"]))


def left_out(project: Project, sample_id: int | None = None) -> dict[int, str]:
    """The label ids left out of reviewer agreement by an ``exclude`` choice that applies, to their package id."""
    out: dict[int, str] = {}
    for pkg in packages(project):
        if exclude_applies(pkg):
            for lid in pkg["excluded_label_ids"]:
                out[int(lid)] = pkg["package_id"]
    if sample_id is not None and out:
        keep = {r[0] for r in project.store.query(
            f"SELECT label_id FROM labels WHERE sample_id=? AND label_id IN ({','.join('?' * len(out))})",
            (sample_id, *out))}
        out = {k: v for k, v in out.items() if k in keep}
    return out


def _used_text(project: Project, pkg: dict) -> str:
    """What an imported package's labels were used for (recall-final-check-fixes D7): a record whose reference label
    an earlier recall look sealed before the import keeps that reference, so the package's label is for agreement only."""
    st = project.store
    # History change ids, not times: a look and an import in the same second stay ordered
    first = st.scalar("SELECT MIN(change_id) FROM history WHERE kind='package_import' AND undone_by IS NULL "
                      "AND items LIKE ?", (f'%"{pkg["package_id"]}"%',))
    if first is None:
        return "labels used for agreement and final labels"
    sealed = sum(1 for it in items(project, pkg["package_id"]) if st.scalar(
        "SELECT 1 FROM label_seals WHERE sample_id=? AND record_id=? AND first_change_id<?",
        (it["sample_id"], it["record_id"], first)))
    if not sealed:
        return "labels used for agreement and final labels"
    if sealed >= pkg["n_items"]:
        return "labels used for agreement only; all its records had reference labels sealed by an earlier recall look"
    return (f"labels used for agreement, and for final labels except on {sealed} record{'s' if sealed != 1 else ''} "
            "whose reference labels were sealed by an earlier recall look")


def left_out_line(n: int, package_ids: list[str]) -> str:
    return f"{n} label{'s' if n != 1 else ''} left out by review package {_and(sorted(set(package_ids)))}"


def blinded_value(made_in: str | None, repeat: bool = False) -> str:
    """``yes`` (a first label in a package), ``repeat`` (a re-reviewed one), ``queue`` (a blind queue), ``imported``
    (a file or sample import, label-completion-and-sealing D11), else ``no`` (D7). A label made in WIDER outside a
    blind queue is never blinded."""
    if made_in and made_in.startswith("p-"):
        return "repeat" if repeat else "yes"
    if made_in in ("file import", "sample import"):
        return "imported"
    return "queue" if made_in == "blind queue" else "no"


def _replaced(project: Project, package_id: str, sample_id: int, record_id: int, reviewer: str) -> dict | None:
    """The label a repeat item's package label replaced, from the before-image of the ``package_import`` History
    change that imported it (every column journalled, ``made_in`` included), with ``_basis`` true when a stated basis
    of independence was dropped in the same change (label-completion-and-sealing D11). None without a before-image:
    an import whose History is gone, or an item with no earlier label."""
    found = None
    key = [sample_id, record_id, reviewer]
    for ch in project.store.query("SELECT items FROM history WHERE kind='package_import' "
                                  "ORDER BY change_id"):
        items_ = json.loads(ch["items"] or "[]")
        lab = next((i for i in items_ if i.get("t") == "rows" and i.get("table") == "labels" and i.get("params") == key
                    and i.get("before")), None)
        if lab is None or (lab["before"][0].get("made_in") or "") == package_id:
            continue
        basis = any(i.get("t") == "rows" and i.get("table") == "label_independence" and i.get("params") == key
                    and i.get("before") for i in items_)
        found = {**lab["before"][0], "_basis": basis}
    return found


def repeats(project: Project) -> set[tuple]:
    """(package_id, sample_id, record_id) of every repeat item."""
    return {(r[0], r[1], r[2]) for r in project.store.query(
        "SELECT package_id, sample_id, record_id FROM review_package_items WHERE repeat=1")}


def label_blinded(project: Project, lab: dict, reps: set | None = None) -> str:
    reps = repeats(project) if reps is None else reps
    return blinded_value(lab.get("made_in"), (lab.get("made_in"), lab["sample_id"], lab["record_id"]) in reps)


# ------------------------------------------------------------------------------------------------ making
def _samples(project: Project, names: list[str]) -> list[dict]:
    names = [n for n in dict.fromkeys(n.strip() for n in names or []) if n]
    if not names:
        raise PackageError("give at least one sample")
    try:
        return [lb.sample_by_name(project, n) for n in names]
    except KeyError as e:
        raise PackageError(str(e.args[0]), 404) from e


def _first_labels(project: Project, reviewer: str, mode: str, pairs: list[tuple]) -> list[dict]:
    """The labels the package's criteria version comes from (D2): in ``second`` mode the first label on each record
    (as the queue counts it), in ``independent`` mode every other reviewer's label on the items."""
    out = []
    for sid, rid in pairs:
        labs = [dict(r) for r in project.store.query(
            "SELECT * FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id<>? ORDER BY label_id", (sid, rid, reviewer))]
        out += labs[:1] if mode == "second" else labs
    return out


def _codebook_row(project: Project, version: int) -> dict | None:
    from ..construct.artifacts import list_versions
    return next((r for r in list_versions(project, "codebook") if r["version"] == version), None)


def _criteria(project: Project, reviewer: str, mode: str, pairs: list[tuple], samples: list[dict],
              codebook_version: int | None) -> dict:
    """The codebook version the package carries (Q5): the one the first reviewer's labels used; several need
    ``codebook_version``; none recorded falls back to the latest frozen, else the latest approved version."""
    from ..construct.artifacts import latest, latest_frozen
    first = _first_labels(project, reviewer, mode, pairs)
    used = sorted({int(lab["codebook_version"]) for lab in first if lab["codebook_version"] is not None})
    notes = []
    if codebook_version is not None:
        row = _codebook_row(project, codebook_version)
        if row is None or row["status"] not in ("approved", "frozen"):
            raise PackageError(f"codebook version {codebook_version} is not an approved or frozen version")
        v = codebook_version
    elif len(used) > 1:
        who = sorted({lab["reviewer_id"] for lab in first if lab["codebook_version"] is not None})
        names = _and([s["name"] for s in samples])
        raise PackageError(f"{_and(who)}'s labels on {names} use codebook versions {_and(used)}; choose one with "
                           f"--codebook-version.", 409)
    elif used:
        v = used[0]
    else:
        row = latest_frozen(project, "codebook") or latest(project, "codebook", "approved")
        if row is None:
            raise PackageError("Approve the criteria first", 409)
        v = row["version"]
        notes.append(f"The first reviewer's criteria version is not recorded; the package carries version {v}.")
    row = _codebook_row(project, v)
    return {"version": v, "frozen": int(row is not None and row["status"] == "frozen"), "used": used, "notes": notes}


def _earlier(project: Project, reviewer: str, samples: list[dict]) -> list[dict]:
    """The reviewer's labels on the samples not made in a review package (D7)."""
    q = ",".join("?" * len(samples))
    return [dict(r) for r in project.store.query(
        f"SELECT label_id, sample_id, record_id, made_in FROM labels WHERE reviewer_id=? AND sample_id IN ({q}) "
        f"AND made_in NOT LIKE 'p-%' ORDER BY label_id", (reviewer, *[s["sample_id"] for s in samples]))]


def earlier_message(reviewer: str, earlier: list[dict], page: bool = False) -> str:
    """The refusal of the CLI, or (``page``) the dialog's words, which name its choice instead of the option."""
    n, k = len(earlier), sum(1 for e in earlier if e["made_in"] == "blind queue")
    queue = f", {k} of them from the blind queue" if k else ""
    tail = "choose below whether to retain, re-review or exclude them." if page else \
        "choose --earlier retain, re-review or exclude."
    return (f"{reviewer} already has {n} label{'s' if n != 1 else ''} on these samples that were not made in a review "
            f"package{queue}; {tail}")


def _abstract_versions(project: Project, record_id: int) -> tuple[int | None, int | None]:
    """(the version the model judged, the current version): the latest ``ok`` model call whose provider is not the
    researcher, else the current version (D8)."""
    from ..enrich.abstracts import current_version
    cur = current_version(project.store, record_id)
    judged = project.store.scalar("SELECT abstract_version FROM screen_calls WHERE record_id=? AND ok=1 AND "
                                  "COALESCE(provider,'')<>'researcher' AND abstract_version IS NOT NULL "
                                  "ORDER BY call_id DESC LIMIT 1", (record_id,))
    return (judged if judged is not None else cur), cur


def _plan(project: Project, reviewer: str, sample_names: list[str], mode: str, earlier: str | None,
          codebook_version: int | None, preview: bool = False) -> dict:
    """Every check of D2 before anything is written, and what the package would hold."""
    from ..core.journal import _clean, check_name
    reviewer = _clean(reviewer)
    if not reviewer:
        raise PackageError("give the reviewer's name")
    check_name(reviewer)
    samples = _samples(project, sample_names)
    kinds = {s["kind"] for s in samples}
    if len(kinds) > 1:
        raise PackageError("A package holds development or validation records, not both.")
    if mode not in MODES:
        raise PackageError("mode must be second or independent")
    if mode == "second" and kinds != {"probability"}:
        raise PackageError("second mode is for validation samples; use independent for development records.")
    ids = {s["sample_id"] for s in samples}
    for pkg in packages(project):
        if pkg["reviewer"] == reviewer and ids & {x["sample_id"] for x in pkg["samples"]} and is_open(project, pkg):
            names = _and([x["name"] for x in pkg["samples"] if x["sample_id"] in ids])
            raise PackageError(f"{reviewer} already has an open package on {names}; import or withdraw it first.", 409)
    if earlier is not None and earlier not in EARLIER:
        raise PackageError("--earlier must be retain, re-review or exclude")
    # the items: records the reviewer already labelled stay out, whatever made them (D2)
    pairs: list[tuple] = []
    if mode == "second":
        from .design import development_records
        dev = development_records(project)
        self_first, n_pos = 0, 0
        for s in samples:
            sp = lb._second_pairs(project, reviewer, s, dev)
            pairs += sp["pairs"]
            self_first, n_pos = self_first + sp["self_first"], n_pos + sp["n_pos"]
        if self_first and not n_pos:
            raise PackageError(f"{reviewer} made the first labels on {_and([s['name'] for s in samples])}; a "
                               f"second-reviewer package is for someone else.")
    else:
        for s in samples:
            mine = {r[0] for r in project.store.query("SELECT record_id FROM labels WHERE sample_id=? AND reviewer_id=?",
                                                      (s["sample_id"], reviewer))}
            pairs += [(s["sample_id"], r[0]) for r in project.store.query(
                "SELECT record_id FROM sample_rows WHERE sample_id=? ORDER BY queue_pos", (s["sample_id"],))
                if r[0] not in mine]
    from ..construct.artifacts import latest
    if latest(project, "codebook") is None:
        raise PackageError("Save the criteria first: the package carries them.", 409)
    prior = _earlier(project, reviewer, samples)
    if prior and earlier is None and not preview:
        raise PackageError(earlier_message(reviewer, prior) + " " + earlier_meanings(reviewer), 409)
    choice = earlier if prior else None
    rep = []
    if choice == "re-review":
        rep = [(e["sample_id"], e["record_id"]) for e in prior if _in_sample(project, e["sample_id"], e["record_id"])]
    all_pairs = list(dict.fromkeys(pairs + rep))
    if not all_pairs:
        raise PackageError("The package would hold no record.")
    crit = _criteria(project, reviewer, mode, all_pairs, samples, codebook_version) if not preview else None
    if preview:
        try:
            crit = _criteria(project, reviewer, mode, all_pairs, samples, codebook_version)
        except PackageError as e:   # several versions: the dialog offers them in a select
            if "--codebook-version" not in str(e):
                raise
            used = sorted({int(lab["codebook_version"]) for lab in _first_labels(project, reviewer, mode, all_pairs)
                           if lab["codebook_version"] is not None})
            crit = {"version": None, "frozen": 0, "used": used, "notes": [str(e)]}
    versions = {}
    older = 0
    for sid, rid in all_pairs:
        judged, cur = _abstract_versions(project, rid)
        versions[(sid, rid)] = judged
        older += judged is not None and cur is not None and judged < cur
    notes = list(crit["notes"])
    if older:
        notes.append(f"{older} item{' shows' if older == 1 else 's show'} an older abstract than the current one, "
                     f"because the model judged {'that one' if older == 1 else 'those'}.")
    if crit["version"] is not None and not crit["frozen"]:
        notes.append(f"Codebook version {crit['version']}: {NOT_FROZEN}")
    return {"reviewer": reviewer, "samples": samples, "kind": KIND_WORD[kinds.pop()], "mode": mode, "pairs": pairs,
            "repeat": set(rep), "all_pairs": all_pairs, "earlier": choice, "prior": prior, "criteria": crit,
            "abstract_versions": versions, "notes": notes}


def preview(project: Project, reviewer: str, sample_names: list[str], mode: str, earlier: str | None = None,
            codebook_version: int | None = None) -> dict:
    """What the make dialog shows before "Download" (D4): the leaving sentence, the item count, the earlier labels
    and their meanings, and the codebook versions the CLI accepts. Writes nothing."""
    pl = _plan(project, reviewer, sample_names, mode, earlier, codebook_version, preview=True)
    crit = pl["criteria"]
    out = {"reviewer": pl["reviewer"], "n_items": len(pl["all_pairs"]), "kind": pl["kind"], "mode": mode,
           "codebook_version": crit["version"], "frozen": bool(crit["frozen"]),
           "versions": crit["used"] if len(crit["used"]) > 1 else ([crit["version"]] if crit["version"] else []),
           "leaving": leaving_text(len(pl["all_pairs"]), crit["version"], pl["reviewer"]) if crit["version"] else None,
           "leaving_by_version": {str(v): leaving_text(len(pl["all_pairs"]), v, pl["reviewer"]) for v in crit["used"]},
           "earlier": None, "notes": pl["notes"]}
    if pl["prior"]:
        out["earlier"] = {"n": len(pl["prior"]), "queue": sum(1 for e in pl["prior"] if e["made_in"] == "blind queue"),
                          "message": earlier_message(pl["reviewer"], pl["prior"]),
                          "page_message": earlier_message(pl["reviewer"], pl["prior"], page=True),
                          "meanings": earlier_meanings(pl["reviewer"]), "choice": pl["earlier"]}
    return out


def _cells(project: Project) -> dict | None:
    from ..construct.artifacts import latest, latest_frozen, load
    row = latest_frozen(project, "cells") or latest(project, "cells", "approved")
    return load(project, "cells", row["version"])[1] if row else None


def page_data(project: Project, pkg: dict, its: list[dict]) -> dict:
    """The data a package's page holds (D3), and nothing else."""
    from ..construct.artifacts import load
    from ..enrich.abstracts import source_word
    cb = load(project, "codebook", pkg["codebook_version"])[1] or {}
    cells = _cells(project) or {}
    questions = [q for q in [cb.get("question")] + [c.get("question") for c in cells.get("cells") or []] if q]
    constructs = []
    if cells.get("construct"):
        constructs.append({"name": "Construct", "definition": cells["construct"]})
    for d in cells.get("dimensions") or []:
        for v in d.get("values") or []:
            constructs.append({"name": f"{d.get('name', '')}: {v.get('label', '')}", "definition": v.get("definition", "")})
    data_items = []
    for it in its:
        m = project.store.one("SELECT title, authors, year, journal, doi, abstract, abstract_source, abstract_words "
                              "FROM records WHERE record_id=?", (it["record_id"],))
        m = dict(m) if m else {}
        v = project.store.one("SELECT abstract, source, words, at FROM record_abstracts WHERE record_id=? AND version=?",
                              (it["record_id"], it["abstract_version"])) if it["abstract_version"] is not None else None
        data_items.append({
            "item": it["token"], "title": m.get("title") or "",
            "abstract": (v["abstract"] if v else m.get("abstract")) or "",
            "abstract_source": source_word(v["source"] if v else m.get("abstract_source") or "export"),
            "abstract_words": (v["words"] if v else m.get("abstract_words")) or 0,
            "abstract_date": ((v["at"] if v else None) or "")[:10],
            "authors": m.get("authors") or "", "year": m.get("year"), "journal": m.get("journal") or "",
            "doi": m.get("doi") or ""})
    return {"package_id": pkg["package_id"], "project_id": project_id(project),
            "project_title": project.config.title or project.config.name, "reviewer": pkg["reviewer"],
            "codebook_version": pkg["codebook_version"], "frozen": bool(pkg["frozen"]),
            "not_frozen": "" if pkg["frozen"] else NOT_FROZEN, "questions": questions, "constructs": constructs,
            "target": cb.get("target_description") or "",
            "elements": [{"id": e.get("id"), "name": e.get("name", ""), "rule": e.get("rule", ""),
                          "counts": list(e.get("counts") or []), "does_not_count": list(e.get("does_not_count") or [])}
                         for e in cb.get("elements") or []],
            "exclusion_reasons": [{"code": r.get("code"), "text": r.get("text", "")} for r in cb.get("exclusion_reasons") or []],
            "limits_text": TEXTS["limits"], "made_at": pkg["made_at"], "items": data_items}


def make(project: Project, reviewer: str, sample_names: list[str], mode: str, earlier: str | None = None,
         codebook_version: int | None = None, out: str | Path | None = None, by: str | None = None) -> dict:
    """Make a review package (D2): every check first, then one change of kind ``review_package`` that can never be
    undone, the page written to ``review-packages/<id>.html`` and copied to ``out``. Returns the package row with
    ``leaving`` (said before the file is written), ``notes``, ``file`` and ``change_id``."""
    from ..core import researchers
    from ..core.journal import last_change_id, recording
    from .package_page import render
    pl = _plan(project, reviewer, sample_names, mode, earlier, codebook_version)
    reviewer, crit, now = pl["reviewer"], pl["criteria"], utcnow()
    pid = f"p-{now[:10].replace('-', '')}-{secrets.token_hex(3)}"
    pairs = list(pl["all_pairs"])
    random.Random(f"{project.config.seed}:package:{reviewer}:{pid}").shuffle(pairs)
    rel = f"{FOLDER}/{pid}.html"
    pkg = {"package_id": pid, "reviewer": reviewer, "mode": mode, "kind": pl["kind"],
           "samples": [{"sample_id": s["sample_id"], "name": s["name"], "n": project.store.scalar(
               "SELECT COUNT(*) FROM sample_rows WHERE sample_id=?", (s["sample_id"],))} for s in pl["samples"]],
           "codebook_version": crit["version"], "frozen": crit["frozen"], "earlier": pl["earlier"],
           "excluded_label_ids": [e["label_id"] for e in pl["prior"]] if pl["earlier"] == "exclude" else [],
           "n_items": len(pairs), "file": rel, "made_at": now, "imported_at": None, "last_export_seq": None,
           "last_entries": None, "withdrawn_at": None, "withdraw_reason": None}
    its = [{"package_id": pid, "token": secrets.token_urlsafe(12), "sample_id": sid, "record_id": rid,
            "abstract_version": pl["abstract_versions"].get((sid, rid)), "repeat": int((sid, rid) in pl["repeat"])}
           for sid, rid in pairs]
    html = render(page_data(project, pkg, its))
    path = project.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")   # first, so a package on record always has its file
    st = project.store
    try:
        with recording(project, "review_package", f"Review package made for {reviewer} ({len(pairs)} records)",
                       author=by) as rec:
            rec.rows("review_packages", "package_id=?", (pid,))
            rec.rows("review_package_items", "package_id=?", (pid,))
            with st.tx() as c:
                c.execute("INSERT INTO review_packages(package_id,reviewer,mode,kind,samples,codebook_version,frozen,earlier,"
                          "excluded_label_ids,n_items,file,made_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (pid, reviewer, mode, pkg["kind"], json.dumps(pkg["samples"]), crit["version"], crit["frozen"],
                           pl["earlier"], json.dumps(pkg["excluded_label_ids"]), len(pairs), rel, now))
                c.executemany("INSERT INTO review_package_items(package_id,token,sample_id,record_id,abstract_version,repeat) "
                              "VALUES(?,?,?,?,?,?)", [(i["package_id"], i["token"], i["sample_id"], i["record_id"],
                                                        i["abstract_version"], i["repeat"]) for i in its])
    except Exception:
        path.unlink(missing_ok=True)
        raise
    researchers.register(project, reviewer, "review package")
    if out:
        Path(out).write_bytes(path.read_bytes())
    return {**pkg, "leaving": leaving_text(len(pairs), crit["version"], reviewer), "notes": pl["notes"],
            "path": str(path), "change_id": last_change_id(), "download_name": download_name(pkg)}


def download_name(pkg: dict) -> str:
    return f"review-package-{slug(pkg['reviewer'])}-{pkg['package_id']}.html"


def package_file(project: Project, package_id: str, out: str | Path | None = None) -> Path:
    """The kept copy of a package's page; with ``out``, written there again byte for byte."""
    pkg = _row(project, package_id)
    path = project.root / pkg["file"]
    if not path.exists():
        raise PackageError(f"the kept copy of {package_id} is missing from {FOLDER}/", 409)
    if out:
        Path(out).write_bytes(path.read_bytes())
    return path


# ------------------------------------------------------------------------------------------------ status and withdraw
def _state_line(project: Project, pkg: dict, decided: int) -> str:
    if pkg["withdrawn_at"]:
        return f"withdrawn {_date(pkg['withdrawn_at'])}: {pkg['withdraw_reason']}"
    if not pkg["imported_at"]:
        return f"out since {_date(pkg['made_at'])}, not imported"
    return f"imported {_date(pkg['imported_at'])} ({decided} of {pkg['n_items']} decided)"


def status(project: Project) -> dict:
    """What `wider blinding status` and GET /api/blinding/status show (D6): the two texts, then one row per package
    with the lines said once (a sample that changed after the package, a withdraw after an import)."""
    rows = []
    for pkg in packages(project):
        its = items(project, pkg["package_id"])
        n_lab = project.store.scalar("SELECT COUNT(*) FROM labels WHERE made_in=?", (pkg["package_id"],))
        decided = sum(1 for it in its if project.store.one(
            "SELECT 1 FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=? AND made_in=?",
            (it["sample_id"], it["record_id"], pkg["reviewer"], pkg["package_id"])))
        lines = []
        for s in pkg["samples"]:
            now = project.store.scalar("SELECT COUNT(*) FROM sample_rows WHERE sample_id=?", (s["sample_id"],))
            if now > s["n"]:
                lines.append(f"{s['name']} gained {now - s['n']} records after this package was made; they are not in it")
            elif now < s["n"]:
                lines.append(f"{s['name']} lost {s['n'] - now} records after this package was made")
        if pkg["withdrawn_at"] and pkg["imported_at"]:
            tail = ", and the labels it left out stay left out." if pkg["earlier"] == "exclude" else "."
            lines.append(f"{pkg['package_id']} was withdrawn after its decisions were imported; its {n_lab} labels stay{tail}")
        rows.append({"package_id": pkg["package_id"], "reviewer": pkg["reviewer"], "mode": pkg["mode"],
                     "kind": pkg["kind"], "samples": [s["name"] for s in pkg["samples"]], "n_items": pkg["n_items"],
                     "codebook_version": pkg["codebook_version"], "frozen": bool(pkg["frozen"]), "earlier": pkg["earlier"],
                     "made_at": pkg["made_at"], "imported_at": pkg["imported_at"], "withdrawn_at": pkg["withdrawn_at"],
                     "withdraw_reason": pkg["withdraw_reason"], "decided": decided, "labels": n_lab,
                     "open": is_open(project, pkg), "state": _state_line(project, pkg, decided), "lines": lines,
                     "download_name": download_name(pkg)})
    return {"texts": [TEXTS["reviewers"], TEXTS["limits"]], "packages": rows}


def withdraw(project: Project, package_id: str, reason: str, by: str | None = None) -> dict:
    """Withdraw a package with a reason (D6), one undoable change of kind ``package_withdraw``. Labels it already
    brought stay; its ``exclude`` choice ends only if no import came first."""
    from ..core.journal import last_change_id, recording
    reason = (reason or "").strip()
    if not reason:
        raise PackageError("Give a reason.")
    pkg = _row(project, package_id)
    if pkg["withdrawn_at"]:
        raise PackageError(f"Package {package_id} is already withdrawn.", 409)
    with recording(project, "package_withdraw", f"Review package {package_id} withdrawn: {reason}"[:400], author=by) as rec:
        rec.rows("review_packages", "package_id=?", (package_id,))
        project.store.execute("UPDATE review_packages SET withdrawn_at=?, withdraw_reason=? WHERE package_id=?",
                              (utcnow(), reason, package_id))
    return {**_row(project, package_id), "change_id": last_change_id()}


# ------------------------------------------------------------------------------------------------ import
def _entries_key(entries: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorted(entries, key=lambda e: str(e.get("item"))), sort_keys=True,
                                     default=str).encode()).hexdigest()


def _hhmm(at: str | None) -> str:
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return str(at or "")


UNREADABLE = "This decisions file cannot be read: it is not a decisions file of a review package."


def _shape(data: Any) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
        raise PackageError(UNREADABLE)
    return data


def read_decisions(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise PackageError(UNREADABLE) from e
    return _shape(data)


def import_file(project: Project, path: str | Path | None = None, data: dict | None = None, by: str | None = None) -> dict:
    """Import a reviewer's decisions file (D5) into the package that made it, as one undoable change of kind
    ``package_import`` signed by the researcher; each label is the package reviewer's, marked with the package."""
    from ..core import researchers
    from ..core.journal import MAX_AUTHOR, last_change_id, recording
    data = read_decisions(path) if data is None else _shape(data)
    st = project.store
    r = st.one("SELECT package_id FROM review_packages WHERE package_id=?", (str(data.get("package_id") or ""),))
    if r is None:
        raise PackageError(FOREIGN, 404)
    if str(data.get("project_id") or "") != project_id(project):
        raise PackageError(FOREIGN)
    pkg = _row(project, r["package_id"])
    pid, reviewer = pkg["package_id"], pkg["reviewer"]
    if pkg["withdrawn_at"]:
        raise PackageError(f"Package {pid} was withdrawn; its decisions are not imported.", 409)
    try:
        seq = int(data.get("export_seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    entries = [e for e in data.get("items") or [] if isinstance(e, dict)]
    key = _entries_key(entries)
    last = json.loads(pkg["last_entries"] or "{}")
    if pkg["last_export_seq"] is not None:
        if seq < pkg["last_export_seq"]:
            raise PackageError(f"This file is older than the one already imported from package {pid} "
                               f"(exported {_hhmm(last.get('exported_at'))}).", 409)
        if seq == pkg["last_export_seq"]:
            if last.get("key") == key:
                return {"package_id": pid, "reviewer": reviewer, "nothing_new": True, "imported": 0,
                        "lines": ["Nothing new: this file was already imported."], "change_id": None}
            raise PackageError("This decisions file has the export number of one already imported, with other entries. "
                               "Export again from the page.", 409)
    by_token = {it["token"]: it for it in items(project, pid)}
    n = Counter()
    todo = []
    kept: list[int] = []
    for e in entries:
        it = by_token.get(str(e.get("item") or ""))
        if it is None:
            n["unknown"] += 1
            continue
        dec = str(e.get("decision") or "").strip().lower()
        if not dec:
            continue
        if dec not in lb.DECISIONS:
            n["bad"] += 1
            continue
        if not _in_sample(project, it["sample_id"], it["record_id"]):
            n["left"] += 1
            continue
        old = st.one("SELECT made_in, updated_at FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                     (it["sample_id"], it["record_id"], reviewer))
        if old is not None and old["made_in"] != pid and (not it["repeat"] or (old["updated_at"] or "") > pkg["made_at"]):
            kept.append(it["record_id"])   # made or changed in WIDER after the package: never overwritten (risk R2)
            continue
        todo.append((it, dec, e))
    lines = []
    named = str(data.get("reviewer") or "").strip()
    if named and named != reviewer:
        lines.append(f"The file names {named}; its labels are stored as {reviewer}'s, the package's reviewer.")
    skipped = []
    if n["unknown"]:
        skipped.append(f"{n['unknown']} item{'s are' if n['unknown'] != 1 else ' is'} not in this package")
    if n["bad"]:
        skipped.append(f"{n['bad']} decision{'s are' if n['bad'] != 1 else ' is'} not include, exclude or maybe")
    if skipped:
        lines.append(f"{' and '.join(skipped)}; they were skipped.")
    if kept:
        lines.append(f"{len(kept)} of these records already have a label by {reviewer} made or changed in WIDER after the "
                     f"package was made; {'those labels were' if len(kept) != 1 else 'that label was'} kept and the "
                     f"file's {'entries were' if len(kept) != 1 else 'entry was'} not imported.")
    if n["left"]:
        lines.append(f"{n['left']} item{'s' if n['left'] != 1 else ''} left the sample since the package was made; "
                     f"{'they were' if n['left'] != 1 else 'it was'} skipped.")
    rows = {s["sample_id"]: st.one("SELECT sample_id, name, kind, locked FROM samples WHERE sample_id=?", (s["sample_id"],))
            for s in pkg["samples"]}
    rows = {sid: dict(r) for sid, r in rows.items() if r is not None}
    names = {sid: r["name"] for sid, r in rows.items()}
    looked = {sid: bool(st.scalar("SELECT COUNT(*) FROM validation_looks WHERE sample_id=? AND first_at<?",
                                  (sid, pkg["made_at"]))) for sid in names}
    lb.add_reviewer(project, reviewer)
    summary = f"{reviewer[:MAX_AUTHOR]}'s decisions imported from review package {pid} ({len(todo)} labels)"
    with recording(project, "package_import", summary, author=by) as rec:
        rec.rows("review_packages", "package_id=?", (pid,))
        rec.rows("label_revisions", "package_id=?", (pid,))
        if any(r["locked"] for r in rows.values()):
            rec.extra({"t": "while_locked"})   # read by the undo rule (D7)
        for it, _, _ in todo:
            rec.rows("labels", "sample_id=? AND record_id=? AND reviewer_id=?", (it["sample_id"], it["record_id"], reviewer))
        with st.tx():
            for it, dec, e in todo:
                s = {"sample_id": it["sample_id"], "name": names.get(it["sample_id"])}
                ticks = e.get("ticks") if isinstance(e.get("ticks"), dict) else {}
                ticks = {str(k): str(v) for k, v in ticks.items()}
                tags = [str(t).strip() for t in (e.get("tags") or []) if str(t).strip()] if isinstance(e.get("tags"), list) else []
                old = st.one("SELECT * FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                             (it["sample_id"], it["record_id"], reviewer))
                reason = str(e.get("reason") or "")[:500]
                note = str(e.get("note") or "")[:4000]
                lb._save_label(project, s, it["record_id"], reviewer, dec, reason, ticks, note, rec, made_in=pid,
                               state=(pkg["codebook_version"], pkg["frozen"], int(looked.get(it["sample_id"], False))),
                               abstract_version=it["abstract_version"], tags=tags)
                if old is None:
                    continue
                if it["repeat"] and old["made_in"] != pid:   # a re-review replaces the earlier label
                    cause = "package re-review"
                elif (old["made_in"] == pid and it["sample_id"] in rows and lb._guarded(project, rows[it["sample_id"]], it["record_id"])
                      and not lb._equal(old, dec, reason, ticks, note)):
                    cause = "package update"   # a later export over a locked or sealed label keeps the old value (F3)
                else:
                    continue
                st.execute("INSERT INTO label_revisions(label_id,sample_id,record_id,reviewer,old_decision,old_reason,"
                           "old_labels,old_codebook_version,new_decision,new_reason,new_labels,new_codebook_version,"
                           "at,cause,package_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (old["label_id"], it["sample_id"], it["record_id"], reviewer, old["decision"], old["reason"],
                            old["labels"], old["codebook_version"], dec, reason, json.dumps(ticks),
                            pkg["codebook_version"], utcnow(), cause, pid))
            st.execute("UPDATE review_packages SET imported_at=?, last_export_seq=?, last_entries=? WHERE package_id=?",
                       (utcnow(), seq, json.dumps({"key": key, "exported_at": data.get("exported_at")}), pid))
    researchers.register(project, reviewer, "review package")
    decided = len(todo)
    lines.insert(0, f"{reviewer}: {decided} of {pkg['n_items']} decided; {decided} imported.")
    unfinished = _other_unfinished(project, pkg) if pkg["mode"] == "independent" else None   # a second-mode package
    # holds the first reviewer's records only, so other second reviewers are not owed labels on it
    if unfinished:
        lines.append(unfinished)
    return {"package_id": pid, "reviewer": reviewer, "n_items": pkg["n_items"], "decided": decided, "imported": decided,
            "skipped": {**dict(n), **({"kept_wider": len(kept)} if kept else {})}, "lines": lines, "change_id": last_change_id(), "nothing_new": False}


def _other_unfinished(project: Project, pkg: dict) -> str | None:
    """The line said once when another reviewer of the samples still has package records without a label (D5.6)."""
    recs: dict[int, set] = {}
    for it in items(project, pkg["package_id"]):
        recs.setdefault(it["sample_id"], set()).add(it["record_id"])
    others = sorted({r[0] for sid in recs for r in project.store.query(
        "SELECT DISTINCT reviewer_id FROM labels WHERE sample_id=? AND reviewer_id<>?", (sid, pkg["reviewer"]))})
    out = []
    for o in others:
        missing = sum(len(rs - {r[0] for r in project.store.query(
            "SELECT record_id FROM labels WHERE sample_id=? AND reviewer_id=?", (sid, o))}) for sid, rs in recs.items())
        if missing:
            out.append(f"{o} has not labelled {missing} of these records yet. {pkg['reviewer']}'s labels are now visible "
                       f"in WIDER; for independent labels, {o} labels those first or works from a review package too.")
    return " ".join(out) or None


# ------------------------------------------------------------------------------------------------ disagreements
def disagreements(project: Project, sample_name: str, a: str, b: str, all: bool = False) -> dict:  # noqa: A002
    """The records where reviewers ``a`` and ``b`` differ (every record both labelled with ``all``), each side with
    decision, reason, ticks, tags, note, how it was made, blinded, left out and its abstract version, and a line when
    the two read different abstracts (D9). No model field."""
    from ..enrich.abstracts import describe
    s = lb.sample_by_name(project, sample_name)
    st = project.store
    reps = repeats(project)
    out_ids = left_out(project, s["sample_id"])
    labs: dict[int, dict[str, dict]] = {}
    for r in st.query("SELECT * FROM labels WHERE sample_id=? AND reviewer_id IN (?,?)", (s["sample_id"], a, b)):
        lab = dict(r)
        labs.setdefault(lab["record_id"], {})[lab["reviewer_id"]] = {
            "decision": lab["decision"], "reason": lab["reason"], "ticks": json.loads(lab["labels"] or "{}"),
            "tags": json.loads(lab["tags"] or "[]"), "note": lab["note"], "made_in": lab["made_in"],
            "blinded": label_blinded(project, lab, reps), "left_out": lab["label_id"] in out_ids,
            "abstract_version": lab["abstract_version"], "abstract": describe(st, lab["record_id"], lab["abstract_version"])}
    adj = {r["record_id"]: dict(r) for r in st.query(
        "SELECT record_id, decision, reason, decided_by, decided_at FROM adjudications WHERE sample_id=?", (s["sample_id"],))}
    ids = [rid for rid, d in labs.items() if a in d and b in d and (all or d[a]["decision"] != d[b]["decision"])]
    titles = {r["record_id"]: r["title"] for r in st.query(
        f"SELECT record_id, title FROM records WHERE record_id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
    rows = []
    for rid in sorted(ids):
        x, y = labs[rid][a], labs[rid][b]
        diff = None
        if x["abstract_version"] != y["abstract_version"]:
            diff = f"{a} and {b} read different abstracts ({x['abstract']}; {y['abstract']})"
        rows.append({"record_id": rid, "title": titles.get(rid), "a": x, "b": y, "agree": x["decision"] == y["decision"],
                     "adjudicated": adj.get(rid), "different_abstracts": diff})
    return {"sample": sample_name, "a": a, "b": b, "locked": bool(s["locked"]), "rows": rows}


# ------------------------------------------------------------------------------------------------ outputs
def validation_out(project: Project, sample: str | None = None) -> list[str]:
    """The sentence said once for each validation package (on ``sample`` when given) neither imported nor withdrawn
    (D11)."""
    out = []
    for pkg in packages(project):
        if sample is not None and sample not in [s["name"] for s in pkg["samples"]]:
            continue
        if pkg["kind"] == "validation" and not pkg["imported_at"] and not pkg["withdrawn_at"]:
            names = _and([s["name"] for s in pkg["samples"]])
            out.append(f"{pkg['reviewer']}'s review package on {names} is out and not imported; disagreements with "
                       f"{pkg['reviewer']} are not known yet, and recall uses the first labels.")
    return out


def counts_by_blinded(project: Project, kind: str = "probability") -> dict[str, int]:
    """Labels on samples of ``kind`` by how they were made: yes, repeat, queue, no (labels left out by an exclude
    choice are not counted, D7)."""
    reps = repeats(project)
    out_ids = left_out(project)
    c = Counter({"yes": 0, "repeat": 0, "queue": 0, "no": 0, "imported": 0})
    for r in project.store.query("SELECT l.* FROM labels l JOIN samples s ON s.sample_id=l.sample_id WHERE s.kind=?", (kind,)):
        lab = dict(r)
        if lab["label_id"] in out_ids:
            continue
        c[label_blinded(project, lab, reps)] += 1
    return dict(c)


def dashboard_line(project: Project) -> dict | None:
    """The Results dashboard line and its counts (results-dashboard, "Review packages"); None without a package."""
    pk = packages(project)
    if not pk:
        return None
    c = counts_by_blinded(project)
    out = [p["reviewer"] for p in pk if p["kind"] == "validation" and not p["imported_at"] and not p["withdrawn_at"]]
    line = (f"Validation labels: {c['yes']} made blind in a review package, {c['repeat']} repeated in a review package, "
            f"{c['queue']} in the blind queue in WIDER, {c['no']} made in WIDER, {c['imported']} imported.")
    for r in out:
        line += f" {r}'s review package is out and not imported yet."
    return {"line": line, "made_blind": c["yes"], "repeated": c["repeat"], "blind_queue": c["queue"],
            "made_in_wider": c["no"], "imported": c["imported"], "not_imported": out}


def report_section(project: Project, who=None) -> dict:
    """The blinded-review facts of the report, the methods draft and ``review_packages.json`` (D11). ``who`` writes a
    reviewer as shared files name them (researcher-attribution)."""
    from .labels import agreement
    name = who or (lambda x: x)
    pk = packages(project)
    st = project.store
    reviewers = {r[0] for r in st.query("SELECT DISTINCT l.reviewer_id FROM labels l JOIN samples s "
                                        "ON s.sample_id=l.sample_id")}
    if not pk:
        return {"packages": [], "one_reviewer": len(reviewers) <= 1 and bool(reviewers), "lines": []}
    reps = repeats(project)
    rows, lines = [], []
    for pkg in pk:
        labs = [dict(r) for r in st.query("SELECT * FROM labels WHERE made_in=?", (pkg["package_id"],))]
        by = Counter(label_blinded(project, lab, reps) for lab in labs)
        kind = pkg["kind"]
        rv = name(pkg["reviewer"])
        role = "second reviewer" if pkg["mode"] == "second" else "independent reviewer"
        dates = _date_span(pkg["made_at"], pkg["imported_at"]) if pkg["imported_at"] else \
            f"made {_long_date(pkg['made_at'])}, not imported"
        used = _used_text(project, pkg) if pkg["imported_at"] else "no labels imported yet"
        line = (f"{rv}: {role} on {pkg['n_items']} {kind} records (criteria version {pkg['codebook_version']}), blind in "
                f"a review package, {dates}; {used}")
        if pkg["withdrawn_at"]:
            line += f"; withdrawn {_long_date(pkg['withdrawn_at'])}: {pkg['withdraw_reason']}"
        lines.append(line)
        if pkg["earlier"] == "re-review" and by["repeat"]:
            lines.append(f"{rv} labelled {by['repeat']} records again; {REPEAT_LIMIT}")
        elif pkg["earlier"] == "retain":
            lines.append(f"{rv}'s earlier labels on these samples were retained as they were made")
        elif pkg["earlier"] == "exclude":
            lines.append(f"{rv}'s {len(pkg['excluded_label_ids'])} earlier labels were left out of reviewer agreement "
                         "and the blinded-review counts")
        # agreement with the first labels on the package's records, with and without the repeats
        agree = {}
        for s in pkg["samples"]:
            others = sorted({r[0] for r in st.query("SELECT DISTINCT reviewer_id FROM labels WHERE sample_id=? AND "
                                                    "reviewer_id<>?", (s["sample_id"], pkg["reviewer"]))})
            for o in others:
                name_s = st.scalar("SELECT name FROM samples WHERE sample_id=?", (s["sample_id"],))
                if name_s is None:
                    continue
                with_r = agreement(project, name_s, o, pkg["reviewer"], only_made_in=pkg["package_id"])
                without = agreement(project, name_s, o, pkg["reviewer"], only_made_in=pkg["package_id"], repeats=False)
                # keyed by the other reviewer as shared files name them
                agree[f"{name_s}:{name(o)}"] = {"with_repeats": {"n": with_r["n"], "kappa": with_r["kappa"]},
                                          "without_repeats": {"n": without["n"], "kappa": without["kappa"]}}
                if with_r["n"]:
                    lines.append(f"Agreement of {name(o)} and {rv} on {name_s} (blinded labels): kappa {with_r['kappa']} "
                                 f"on {with_r['n']} records with repeats, kappa {without['kappa']} on {without['n']} "
                                 f"without")
        resolved = st.scalar("SELECT COUNT(*) FROM adjudications a JOIN labels l ON l.sample_id=a.sample_id AND "
                             "l.record_id=a.record_id WHERE l.made_in=?", (pkg["package_id"],))
        rows.append({"package_id": pkg["package_id"], "reviewer": rv, "mode": pkg["mode"], "sample_kind": kind,
                     "samples": [s["name"] for s in pkg["samples"]], "items": pkg["n_items"],
                     "codebook_version": pkg["codebook_version"], "frozen": bool(pkg["frozen"]),
                     "made_at": pkg["made_at"], "imported_at": pkg["imported_at"], "withdrawn_at": pkg["withdrawn_at"],
                     "earlier": pkg["earlier"], "labels": {"blind": by["yes"], "repeat": by["repeat"]},
                     "agreement": agree, "disagreements_resolved": resolved})
    diff = _different_abstracts(project)
    for (x, y), k in sorted(diff.items()):
        lines.append(f"{k} record{'s were' if k != 1 else ' was'} labelled by {name(x)} and {name(y)} on different "
                     f"abstract versions")
    c = counts_by_blinded(project)
    lines.append(f"Validation labels: {c['yes']} made blind in a review package, {c['repeat']} repeated in a review "
                 f"package, {c['queue']} in the blind queue in WIDER (History visible), {c['no']} made in WIDER, "
                 f"{c['imported']} imported")
    lines.append(TEXTS["limits"])
    return {"packages": rows, "one_reviewer": False, "lines": lines, "counts": c,
            "different_abstracts": [{"a": name(x), "b": name(y), "records": k} for (x, y), k in sorted(diff.items())]}


def _long_date(at: str | None) -> str:
    if not at:
        return ""
    try:
        d = datetime.fromisoformat(at)
    except ValueError:
        return at[:10]
    return f"{d.day} {d.strftime('%B')} {d.year}"


def _date_span(a: str, b: str) -> str:
    """"2 to 9 October 2026", "30 September to 9 October 2026", or both dates in full."""
    try:
        x, y = datetime.fromisoformat(a), datetime.fromisoformat(b)
    except ValueError:
        return f"{_long_date(a)} to {_long_date(b)}"
    if x.date() == y.date():
        return _long_date(b)
    if (x.year, x.month) == (y.year, y.month):
        return f"{x.day} to {_long_date(b)}"
    if x.year == y.year:
        return f"{x.day} {x.strftime('%B')} to {_long_date(b)}"
    return f"{_long_date(a)} to {_long_date(b)}"


def _different_abstracts(project: Project) -> dict[tuple, int]:
    """Pairs of reviewers (one of them a package reviewer) and how many records they labelled on different abstract
    versions."""
    out: Counter = Counter()
    for pkg in packages(project):
        for s in pkg["samples"]:
            mine = {r["record_id"]: r["abstract_version"] for r in project.store.query(
                "SELECT record_id, abstract_version FROM labels WHERE sample_id=? AND made_in=?",
                (s["sample_id"], pkg["package_id"]))}
            for r in project.store.query("SELECT reviewer_id, record_id, abstract_version FROM labels WHERE sample_id=? "
                                         "AND reviewer_id<>?", (s["sample_id"], pkg["reviewer"])):
                if r["record_id"] in mine and r["abstract_version"] is not None and mine[r["record_id"]] is not None \
                        and r["abstract_version"] != mine[r["record_id"]]:
                    out[(r["reviewer_id"], pkg["reviewer"])] += 1
    return dict(out)


def bundle_json(project: Project, who=None) -> list[dict]:
    """``review_packages.json`` of the share bundle: the facts of each package, no item token (D11)."""
    sec = report_section(project, who)
    return [{k: v for k, v in p.items()} for p in sec["packages"]]


def to_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)
