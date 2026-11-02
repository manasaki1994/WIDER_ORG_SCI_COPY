"""Benchmark coverage: which known papers each platform export (and the deduplicated corpus) retrieved.

Development papers are reported at any time. Validation papers are reported only once the synonyms
artifact is frozen or the uploads are marked final (``identification.validation_after_freeze``), because a query tuned on the validation set has no
honest validation left. Asking for them earlier is allowed but recorded (:func:`look`): the time goes to the store
meta key ``validation_peeked_at`` (``validation_peeks`` counts the looks), the papers shown to
``validation_seen_by_look`` and the history gets a ``validation_peek`` change, which cannot be undone. Before the
first counting unlock, and while the query or the uploads are reopened, the researcher first chooses whether the
papers shown count as seen (D3, D21, unlock-seen-choice D7). After an unlock
only papers that entered Validation since count (:func:`validation_papers`); :func:`validity_note` says whether the
search is validated.
"""
from __future__ import annotations

import contextlib
import contextvars

from ..construct import artifacts
from ..construct.artifacts import CHOICE_TAIL, SeenChoiceNeeded, papers_phrase
from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.provenance import utcnow

SEEN_BY_LOOK = "validation_seen_by_look"   # meta: JSON list of {benchmark_id, doi, title_norm, unlock_no, counted,
#                                             look, at, kind, choice, after_unlock}: one entry per paper shown by a
#                                             look (D3; choice and after_unlock since unlock-seen-choice D7)
LOOK_EARLY = "validation set shown before the query was frozen; this look is logged in the history"
LOOK_REOPENED = "validation set shown while the {what} reopened; this look is logged in the history"
HIDDEN_NOTE = "Validation set hidden until the query is frozen or the uploads are marked final."
BOTH_NOTE = "Development and validation sets."   # the coverage card's label
# the same state in report text (the report, the methods draft, the package and the Recall page): a whole sentence
BOTH_TEXT = "Seed papers were split into a development set and a validation set."
NOT_COUNTED = "from the query's results: kept, not counted in validation"   # seed.SOURCE_LABELS["query_results"]
USED = "Validation, used"   # Validation papers shown before the query was revised or the uploads reopened (D1)
MOVED_KEY = "validation_moved"   # meta: JSON list of {benchmark_id, doi, title_norm, at, by}: shown papers moved (D3)
USED_TITLE = ("used validation paper: seen before the query was revised or the uploads reopened; recall on it is not "
              "independent")
HIDING_OFF = "hiding_off"   # look kind: the papers shown while validation hiding was off, recorded when it went back on
HIDING_ON_SUMMARY = ("validation hiding turned back on: {n} validation papers shown while it was off recorded as seen "
                     "(kept as validation)")
WAS_DEV_TITLE = "in Validation, but was in Development: counted with Development"

# a seed paper's source in words ("By source: researcher 2" read as a cipher, inspection 1 Nov 2026)
SOURCE_WORDS = {"prior_review": "from a prior review", "researcher": "added by the researcher",
                "colleague": "from a colleague", "other_database": "from another database",
                "query_results": "picked from the query results",
                "seed_chain": "found through the seeds' citations"}


def source_words(source: str) -> str:
    if source.startswith("seed_chain:"):
        return f"found from seed {source.split(':', 1)[1]}"
    return SOURCE_WORDS.get(source, source)


def source_counts(by: dict) -> str:
    """{"researcher": 2} → "2 added by the researcher"; a value (found, n) reads "f of n ..."."""
    return ", ".join(f"{v[0]} of {v[1]} {source_words(k)}" if isinstance(v, (tuple, list)) else f"{v} {source_words(k)}"
                     for k, v in by.items())



_DEV_ONLY = contextvars.ContextVar("wider_development_only", default=False)


@contextlib.contextmanager
def development_only():
    """Within it the validation set counts as hidden, whatever the freeze: the replay record (2c D0b) hashes the
    Development seeds only, so freezing a version or marking uploads final changes none of its hashes."""
    tok = _DEV_ONLY.set(True)
    try:
        yield
    finally:
        _DEV_ONLY.reset(tok)


def seen_count(project: Project) -> int:
    """The seen count (D3): the unlocks that count as seen (``counts_as_seen`` 1)."""
    r = project.store.query("SELECT COUNT(*) AS n FROM query_unlocks WHERE counts_as_seen=1")
    return int(r[0]["n"]) if r else 0


def _key(b) -> tuple[str, str]:
    from ..core.text import clean_doi, norm_title
    return clean_doi(b.doi) or "", norm_title(b.title) or ""


def _hit(b, dois: set[str], titles: set[str]) -> bool:
    doi, title = _key(b)
    return (doi and doi in dois) or (title and title in titles) or False


def _look_entries(project: Project) -> list[dict]:
    import json
    return json.loads(project.store.get_meta(SEEN_BY_LOOK) or "[]")


def moved(project: Project) -> list[dict]:
    """The validation papers moved to Development after they had been shown (meta :data:`MOVED_KEY`, D3): one entry
    ``{benchmark_id, doi, title_norm, at, by}`` per move, oldest first."""
    import json
    return json.loads(project.store.get_meta(MOVED_KEY) or "[]")


def seen_keys(project: Project) -> tuple[set[str], set[str]]:
    """DOIs and normalised titles seen: in a counting unlock's ``seen_json``, shown by a counted look or a look whose
    papers were recorded as used (``used_at``), or moved to Development after being shown."""
    import json

    items: list[dict] = []
    for r in project.store.query("SELECT seen_json FROM query_unlocks WHERE counts_as_seen=1"):
        items += json.loads(r["seen_json"] or "[]")
    items += [i for i in _look_entries(project) if i.get("counted") or i.get("used_at")]
    items += moved(project)
    return ({i["doi"] for i in items if i.get("doi")}, {i["title_norm"] for i in items if i.get("title_norm")})


def unlocks(project: Project) -> list[dict]:
    """The list of unlocks (D2), oldest first; ``seen`` is the parsed ``seen_json``."""
    import json

    out = []
    for r in project.store.query("SELECT * FROM query_unlocks ORDER BY unlock_no"):
        d = dict(r)
        d["seen"] = json.loads(d.get("seen_json") or "[]")
        out.append(d)
    return out


def latest_unlock_no(project: Project) -> int:
    """The latest unlock number: ``MAX(unlock_no)`` over every row, counting or not (0 with none)."""
    return int(project.store.scalar("SELECT COALESCE(MAX(unlock_no), 0) FROM query_unlocks") or 0)


def validation_papers(project: Project, papers: list | None = None) -> list:
    """The validation set (D3): Validation papers not from the query's results and not seen (a counting unlock's
    ``seen_json`` or a counted look); once a counting unlock exists, also entered after the latest one and never in
    Development. Whether the set is shown stays the caller's decision (:func:`validation_unlocked`)."""
    from ..core.text import clean_doi, norm_title

    papers = project.read_benchmark() if papers is None else papers
    n = seen_count(project)
    dois, titles = seen_keys(project)
    out = []
    for b in papers:
        if b.set != "Validation" or b.source == "query_results":
            continue
        if (clean_doi(b.doi) or "") in dois or (norm_title(b.title) or "") in titles:
            continue
        if n and (b.validation_since_unlock != n or b.was_development):
            continue
        out.append(b)
    return out


def _shown_index(project: Project) -> tuple[set[str], set[str], set[str], set[str]]:
    """Keys shown as validation papers (D3): look entries, ``seen_json`` entries listed as "Validation" or as used, and
    papers moved to Development after being shown (a former paper moved back stays shown); then the keys of
    ``seen_json`` entries written before entries named their group (read with ``was_development``)."""
    import json

    dois, titles, old_dois, old_titles = set(), set(), set(), set()
    entries = list(_look_entries(project)) + moved(project)
    for r in project.store.query("SELECT seen_json FROM query_unlocks"):
        for e in json.loads(r["seen_json"] or "[]"):
            if "set" not in e:
                old_dois.add(e.get("doi") or "")
                old_titles.add(e.get("title_norm") or "")
            elif e["set"] in ("Validation", USED):
                entries.append(e)
    dois |= {e["doi"] for e in entries if e.get("doi")}
    titles |= {e["title_norm"] for e in entries if e.get("title_norm")}
    return dois, titles, old_dois - {""}, old_titles - {""}


def _shown_ids(project: Project, papers: list) -> set[str]:
    """The ids of the Validation papers (not from the query's results) that have been shown as validation papers."""
    dois, titles, old_dois, old_titles = _shown_index(project)
    now = ({b.benchmark_id for b in validation_papers(project, papers)}
           if validation_unlocked(project) or not project.config.protocol.identification.validation_after_freeze
           else set())
    return {b.benchmark_id for b in papers if b.set == "Validation" and b.source != "query_results"
            and (_hit(b, dois, titles) or (not b.was_development and _hit(b, old_dois, old_titles))
                 or b.benchmark_id in now)}


def has_been_shown(project: Project, paper, papers: list | None = None) -> bool:
    """Whether a Validation paper has been shown as a validation paper (D3): by a look (counted or kept), in the
    validation set at an unlock, or in the validation set shown now."""
    papers = project.read_benchmark() if papers is None else papers
    return paper.benchmark_id in _shown_ids(project, papers)


def used_papers(project: Project, papers: list | None = None) -> list:
    """The used validation papers (D1): Validation papers not from the query's results that no longer count as fresh
    validation and have been shown as validation papers (or were moved to Development after being shown and came
    back). They stay in Validation, apart: recall on them is not independent."""
    papers = project.read_benchmark() if papers is None else papers
    fresh = {b.benchmark_id for b in validation_papers(project, papers)}
    shown = _shown_ids(project, papers)
    m = moved(project)
    mdois, mtitles = {e["doi"] for e in m if e.get("doi")}, {e["title_norm"] for e in m if e.get("title_norm")}
    return [b for b in papers if b.set == "Validation" and b.source != "query_results" and b.benchmark_id not in fresh
            and (b.benchmark_id in shown or _hit(b, mdois, mtitles))]


def development_papers(project: Project, papers: list | None = None) -> list:
    """The Development papers plus the Validation papers counted with Development: neither fresh nor used (a paper
    that was in Development at an unlock and moved to Validation later, D1); papers from the query's results and used
    papers are in neither."""
    papers = project.read_benchmark() if papers is None else papers
    keep = {b.benchmark_id for b in validation_papers(project, papers)} | {b.benchmark_id for b in used_papers(project, papers)}
    return [b for b in papers if b.set == "Development"
            or (b.set == "Validation" and b.source != "query_results" and b.benchmark_id not in keep)]


def former_ids(project: Project, papers: list | None = None) -> set[str]:
    """The Development papers that are former validation papers (moved after being shown, D3)."""
    papers = project.read_benchmark() if papers is None else papers
    m = moved(project)
    dois, titles = {e["doi"] for e in m if e.get("doi")}, {e["title_norm"] for e in m if e.get("title_norm")}
    ids = {e["benchmark_id"] for e in m}
    return {b.benchmark_id for b in papers if b.set == "Development" and (b.benchmark_id in ids or _hit(b, dois, titles))}


def invite_due(project: Project, papers: list | None = None) -> bool:
    """Whether the seed step invites new validation papers (D4): former validation papers exist and no Validation
    paper (not from the query's results, not a moved paper back) entered Validation after the latest move."""
    papers = project.read_benchmark() if papers is None else papers
    m = moved(project)
    if not m or not former_ids(project, papers):
        return False
    last, ids = max(e.get("at") or "" for e in m), {e["benchmark_id"] for e in m}
    return not any(b.set == "Validation" and b.source != "query_results" and b.benchmark_id not in ids
                   and (b.validation_since or b.added_at or "") >= last for b in papers)  # times in whole seconds


def query_results_papers(project: Project, papers: list | None = None) -> list:
    """Validation papers from the query's results: kept, listed under :data:`NOT_COUNTED`, in neither set (D3)."""
    papers = project.read_benchmark() if papers is None else papers
    return [b for b in papers if b.set == "Validation" and b.source == "query_results"]


def set_members(project: Project, include_validation: bool, papers: list | None = None) -> list[tuple[str, object]]:
    """``(group, paper)`` for every listing of the seed papers (D1, D3): "Development" (with the Validation papers
    counted with Development), "Validation" when included, the used validation papers under :data:`USED` (always:
    they were seen, listing them is not a look) and the papers from the query's results under :data:`NOT_COUNTED`.
    Read before a look is recorded, so the papers shown are the ones that counted when asked for."""
    papers = project.read_benchmark() if papers is None else papers
    return ([("Development", b) for b in development_papers(project, papers)]
            + ([("Validation", b) for b in validation_papers(project, papers)] if include_validation else [])
            + [(USED, b) for b in used_papers(project, papers)]
            + [(NOT_COUNTED, b) for b in query_results_papers(project, papers)])


def set_label(group: str, former: bool = False) -> str:
    """The set column of a listing: "Dev", "Dev (former val)", "Val", "Val (used)", or :data:`NOT_COUNTED` in full."""
    if group == USED:
        return "Val (used)"
    if group == NOT_COUNTED:
        return group
    return "Dev (former val)" if (former and group == "Development") else group[:3]


def row_flags(group: str, paper, former: set[str]) -> dict:
    """The flags a listing row carries (D1): ``used``, ``former`` or ``was_development``."""
    if group == USED:
        return {"used": True}
    if group == "Development" and paper.benchmark_id in former:
        return {"former": True}
    if group == "Development" and paper.set == "Validation":
        return {"was_development": True}
    return {}


def validation_unlocked(project: Project) -> bool:
    """The validation set may be shown: a frozen query stands (the latest freeze, not reopened), or the uploads are
    marked final after the latest unlock (``wider import final``). Never inside :func:`development_only`."""
    if _DEV_ONLY.get():
        return False
    if artifacts.frozen_query(project) is not None:
        return True
    from ..ingest.importer import FINAL_KEY, UNLOCK_NO_KEY

    if not project.store.get_meta(FINAL_KEY):
        return False
    return int(project.store.get_meta(UNLOCK_NO_KEY) or 0) == latest_unlock_no(project)


def look_outcome(choice: str, n: int) -> str:
    """The line after the choice of an early look (D3)."""
    if choice == "count":
        return f"Counted as seen: the {papers_phrase(n, 'shown no longer validates', 'shown no longer validate')} the search."
    return f"Kept as validation: the {papers_phrase(n, 'shown still counts', 'shown still count')}; every output states this choice."


def look_plan(project: Project) -> dict:
    """What asking for the hidden validation set now does (D3, unlock-seen-choice D7): ``papers`` (the papers it would
    show), ``kind`` from the state (``query`` once a version was frozen, else ``uploads`` once an unlock row exists,
    else ``before_freeze``), ``early`` (``before_freeze``), ``ask`` (papers to show: the researcher chooses whether
    they count as seen first, in every kind) and ``notice`` (the asking notice)."""
    papers = validation_papers(project)
    if artifacts.latest_frozen(project):
        kind = "query"
    else:
        kind = "uploads" if latest_unlock_no(project) else "before_freeze"
    early = kind == "before_freeze"
    ask = bool(papers)
    n = len(papers)
    when = ("before the query was frozen" if early else
            f"while the {'query is' if kind == 'query' else 'uploads are'} reopened")
    notice = (f"Validation set requested {when}; it holds {n} {'paper' if n == 1 else 'papers'}. "
              f"{CHOICE_TAIL}") if ask else None
    return {"papers": papers, "early": early, "ask": ask, "notice": notice, "kind": kind}


def look(project: Project, seen_choice: str | None = None) -> str:
    """One look at the hidden validation set (coverage, ``wider rank validate``, the ablation). With papers in the
    set it needs the researcher's choice (raises :class:`SeenChoiceNeeded`, nothing recorded); ``seen_choice`` is
    ignored otherwise. Records the look and returns the line to show."""
    plan = look_plan(project)
    if plan["ask"] and seen_choice is None:
        raise SeenChoiceNeeded(plan["notice"])
    choice = seen_choice if plan["ask"] else None
    record_peek(project, plan["papers"], counted=choice != "keep", kind=plan["kind"], choice=choice)
    head = (LOOK_EARLY if plan["early"] else
            LOOK_REOPENED.format(what="query is" if plan["kind"] == "query" else "uploads are"))
    return f"{head}. {look_outcome(choice, len(plan['papers']))}" if choice else head


def record_peek(project: Project, papers: list | tuple = (), counted: bool = True, kind: str = "before_freeze",
                choice: str | None = None) -> None:
    """Record one look at the hidden validation set: the time, the look count and every paper shown (with the seen
    count as ``unlock_no``, ``counted``, the researcher's ``choice`` and the latest unlock number as ``after_unlock``)
    in one permanent ``validation_peek`` change."""
    import json

    from ..core import journal
    from ..core.text import clean_doi, norm_title

    if kind == "before_freeze":
        summary = ("validation set shown before the query was frozen" if choice is None else
                   f"validation set looked at before the freeze: {'counted as seen' if choice == 'count' else 'kept as validation'}")
    elif kind == HIDING_OFF:
        summary = HIDING_ON_SUMMARY.format(n=len(papers))
    else:
        summary = f"validation set shown while the {'query is' if kind == 'query' else 'uploads are'} reopened"
        if choice is not None:
            summary += f": {'counted as seen' if choice == 'count' else 'kept as validation'}"
    st = project.store
    with journal.recording(project, "validation_peek", summary) as rec:
        rec.rows("meta", "key IN (?,?,?)", ["validation_peeked_at", "validation_peeks", SEEN_BY_LOOK])
        now = utcnow()
        st.set_meta("validation_peeked_at", now)
        # the count makes every look a change, even two within the same second
        no = int(st.get_meta("validation_peeks") or 0) + 1
        st.set_meta("validation_peeks", str(no))
        if papers:
            n, after = seen_count(project), latest_unlock_no(project)
            items = json.loads(st.get_meta(SEEN_BY_LOOK) or "[]")
            items += [{"benchmark_id": b.benchmark_id, "doi": clean_doi(b.doi) or "", "title_norm": norm_title(b.title) or "",
                       "unlock_no": n, "counted": bool(counted), "look": no, "at": now, "kind": kind,
                       "choice": choice, "after_unlock": after} for b in papers]
            st.set_meta(SEEN_BY_LOOK, json.dumps(items, ensure_ascii=False))


def hiding_turned_on(project: Project, was_on: bool) -> int:
    """Record the papers then in the validation set as shown when validation hiding is turned back on (owner answer
    q-20261020-120242-7ae2): while ``validation_after_freeze`` was off they were shown, so they are recorded like a look
    kept as validation (a later revision records them as used, moving one warns). Call it after the protocol was set
    and before it is saved; it returns the number of papers recorded (0 when the switch did not go from off to on)."""
    if was_on or not project.config.protocol.identification.validation_after_freeze:
        return 0
    papers = validation_papers(project)
    if papers:
        record_peek(project, papers, counted=False, kind=HIDING_OFF)
    return len(papers)


def kept_unused_looks(project: Project) -> list[dict]:
    """The look entries a revision of the query now would record as used (D2 of validation-reuse): kept as
    validation (``counted`` false, no ``used_at``), their paper still in the validation set, with no frozen query and
    the set hidden. Looks before the latest unlock were settled by that unlock's own choice (unlock-seen-choice D8):
    an entry counts only when its ``after_unlock`` is the latest unlock number, an old entry without it only when it
    is not older than the latest unlock row."""
    if not project.config.protocol.identification.validation_after_freeze:
        return []
    if artifacts.frozen_query(project) is not None or validation_unlocked(project):
        return []
    latest = latest_unlock_no(project)
    last_at = (project.store.scalar("SELECT at FROM query_unlocks WHERE unlock_no=?", (latest,)) or "") if latest else ""
    entries = [e for e in _look_entries(project) if not e.get("counted") and not e.get("used_at")
               and (e["after_unlock"] >= latest if "after_unlock" in e else (e.get("at") or "") >= last_at)]
    if not entries:
        return []
    vset = validation_papers(project)
    ids = {b.benchmark_id for b in vset}
    return [e for e in entries if e["benchmark_id"] in ids
            or any(_hit(b, {e.get("doi")} - {""}, {e.get("title_norm")} - {""}) for b in vset)]


def revision_notice(project: Project, new_version: int) -> str | None:
    """What saving synonyms ``new_version`` after a kept look records (D2), or None."""
    kept = kept_unused_looks(project)
    if not kept:
        return None
    n = len(kept)
    at = min(e.get("at", "") for e in kept)
    return (f"You looked at the validation set on {_date(at)} and kept {_count(n, 'paper', 'papers')} as validation. "
            f"Saving v{new_version} revises the query after that look: the {_count(n, 'paper stays', 'papers stay')} "
            f"in Validation, recorded as used; recall on {'it' if n == 1 else 'them'} will not be independent.")


def mark_used(project: Project, version: int, by: str | None = None) -> int | None:
    """Record the papers of the kept looks as used by the save of ``version`` (D2): ``used_at`` and
    ``used_by_version`` on those look entries, in one final ``validation_used`` change. Returns its change id, or
    None when there was nothing to record."""
    import json

    from ..core import journal

    kept = {(e.get("look"), e["benchmark_id"]) for e in kept_unused_looks(project)}
    if not kept:
        return None
    st = project.store
    with journal.recording(project, "validation_used", f"validation papers recorded as used: v{version} saved after "
                           "a look", author=by) as rec:
        rec.rows("meta", "key=?", [SEEN_BY_LOOK])
        now = utcnow()
        items = _look_entries(project)
        for e in items:
            if (e.get("look"), e["benchmark_id"]) in kept:
                e["used_at"], e["used_by_version"] = now, version
        st.set_meta(SEEN_BY_LOOK, json.dumps(items, ensure_ascii=False))
    return rec.change_id


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _revisions(entries: list[dict]) -> list[tuple[dict, int]]:
    """The saves that recorded one look's papers as used: (first entry, number of papers), oldest first."""
    by: dict = {}
    for e in entries:
        if e.get("used_at"):
            by.setdefault((e["used_at"], e.get("used_by_version")), []).append(e)
    return [(es[0], len(es)) for _, es in sorted(by.items(), key=lambda kv: kv[0][0])]


def _chose(counted) -> str:
    return f"the researcher chose to {'count them as seen' if counted else 'keep them as validation'}."


def validity_note(project: Project, papers: list | None = None) -> dict:
    """Whether the search is validated on a held-out set (D3): one note read by coverage, the report, the methods
    draft, the package and the Recall page. ``sentence`` and ``lines`` are D3's words; ``plain`` is true when the note
    is the hidden-set note or "Development and validation sets." with nothing to add (no unlock, look or choice).
    ``detail`` is what the outputs add (:func:`validity_lines`); ``n_found_by_query`` and ``interval`` are None while
    the validation set is hidden."""
    import json
    from collections import Counter

    from ..construct.seed import retrieved_by_query
    from ..ingest.importer import FINAL_KEY
    from .diagnostics import clopper_pearson

    st = project.store
    papers = project.read_benchmark() if papers is None else papers
    gating = bool(project.config.protocol.identification.validation_after_freeze)
    unlocked = validation_unlocked(project)
    rows = unlocks(project)
    counting = [r for r in rows if r["counts_as_seen"]]
    vset = validation_papers(project, papers)
    n_val = len(vset)
    looks: dict[int, list] = {}
    for e in json.loads(st.get_meta(SEEN_BY_LOOK) or "[]"):
        looks.setdefault(int(e.get("look") or 0), []).append(e)
    # a look is before or after an unlock by its after_unlock (unlock-seen-choice D9), an old entry by its unlock_no;
    # the papers shown while hiding was off are not a look while reopened: they keep the old rule (kept as validation)
    def _after(e: dict) -> bool:
        if "after_unlock" in e and e.get("kind") != HIDING_OFF:
            return bool(e["after_unlock"])
        return bool(e.get("unlock_no"))
    # a look whose papers a later save recorded as used counts like a counted look (D2 of validation-reuse)
    revs = {k: [{"at": u["used_at"], "version": u.get("used_by_version"), "n": u_n} for u, u_n in _revisions(es)]
            for k, es in looks.items()}
    before = [{"at": es[0].get("at", ""), "n": len(es), "counted": bool(es[0].get("counted")), "revised": revs[k]}
              for k, es in sorted(looks.items()) if not _after(es[0])]
    after = [{"at": es[0].get("at", ""), "n": len(es), "uploads": es[0].get("kind") == "uploads",
              "choice": es[0].get("choice"), "counted": bool(es[0].get("counted")), "revised": revs[k]}
             for k, es in sorted(looks.items()) if _after(es[0])]
    uploads = [{"at": r["at"], "version": r["from_version"], "n": r["n_shown"], "counted": bool(r["counts_as_seen"])}
               for r in rows if r["cause"] == "uploads" and r["from_version"] is not None and r["n_shown"]]
    freezes = [{"at": r["at"], "version": r["new_version"], "replaced": r["from_version"], "n": r["n_shown"],
                "counted": bool(r["counts_as_seen"])} for r in rows if r["cause"] == "freeze"]
    # every other asked row (an unlock, a save, a file or an undo with no frozen query): one line each (D9)
    asked = [r for r in rows if r.get("seen_choice") and (r["cause"] in ("unlock", "save")
                                                          or (r["cause"] == "uploads" and r["from_version"] is None))]
    before_v5 = st.get_meta("validation_peeked_before_v5")
    counted_before = [x for x in before if x["counted"] or x["revised"]]
    counted_after = [x for x in after if x["choice"] and (x["counted"] or x["revised"])]
    d = _date
    if counting:
        base = f"validation set seen on {d(counting[0]['at'])} ({_count(len(counting), 'unlock', 'unlocks')})"
    elif counted_before:
        base = f"validation set seen on {d(counted_before[0]['at'])} (looked at before the freeze)"
    elif counted_after:
        x = counted_after[0]
        base = (f"validation set seen on {d(x['at'])} (looked at while the "
                f"{'uploads were' if x['uploads'] else 'query was'} reopened)")
    else:
        base = None
    if base:
        sentence = (f"V{base[1:]}; {_count(n_val, 'paper', 'papers')} added since." if n_val else
                    f"Search not validated: {base}; no paper added since.")
        validated = bool(n_val) and gating and unlocked
    elif before_v5:
        sentence = (f"Search not validated: validation set looked at on {d(before_v5)} before the freeze; the papers "
                    "shown were not recorded.")
        validated = False
    else:
        sentence = BOTH_NOTE if (unlocked or not gating) else HIDDEN_NOTE
        validated = bool(n_val) and gating and unlocked
    lines = []
    if base and gating and not unlocked:   # hidden again after an unlock: the hidden-set note leads, D3's sentence follows
        lines.append(sentence)
        sentence, validated = HIDDEN_NOTE, False
    if base and before_v5:
        lines.append(f"Validation set looked at on {d(before_v5)} before the freeze; the papers shown were not recorded.")
    for x in before:
        lines.append(f"Validation set looked at on {d(x['at'])} before the freeze ({_count(x['n'], 'paper', 'papers')} "
                     f"shown); {_chose(x['counted'])}")
        lines += [f"The query was revised on {d(r['at'])} (v{r['version']} saved) after that look: the "
                  f"{_count(r['n'], 'paper shown is', 'papers shown are')} recorded as used." for r in x["revised"]]
    timed = [(x["at"], 0, f"Uploads reopened on {d(x['at'])} while v{x['version']} was the frozen query "
                          f"({papers_phrase(x['n'])} shown); {_chose(x['counted'])}") for x in uploads]
    timed += [(x["at"], 0, f"v{x['version']} was frozen on {d(x['at'])} in place of the frozen query v{x['replaced']} "
                           f"({papers_phrase(x['n'])} shown); {_chose(x['counted'])}") for x in freezes]
    for r in asked:
        shown, chose = f"({papers_phrase(r['n_shown'])} shown); {_chose(r['counts_as_seen'])}", d(r["at"])
        if r["cause"] == "unlock":
            text = f"v{r['from_version']} was unlocked on {chose} into v{r['new_version']} {shown}"
        elif r["cause"] == "save" and r["frozen_no"] is not None:
            text = f"v{r['new_version']} was saved on {chose} after the frozen query v{r['from_version']} {shown}"
        elif r["cause"] == "save":
            text = f"v{r['new_version']} was saved on {chose} while the uploads were final {shown}"
        else:
            text = f"Uploads reopened on {chose} {shown}"
        timed.append((r["at"], 0, text))
    for x in after:
        where = "uploads were" if x["uploads"] else "query was"
        if x["choice"]:   # asked (unlock-seen-choice D7)
            timed.append((x["at"], 1, f"Looked at on {d(x['at'])} while the {where} reopened "
                                      f"({papers_phrase(x['n'])} shown); {_chose(x['counted'])}"))
        else:
            timed.append((x["at"], 1, f"Looked at on {d(x['at'])} while the {where} reopened: "
                                      f"{_count(x['n'], 'paper', 'papers')} shown then "
                                      f"{'counts' if x['n'] == 1 else 'count'} as seen."))
        timed += [(r["at"], 2, f"The query was revised on {d(r['at'])} (v{r['version']} saved) after that look: the "
                               f"{_count(r['n'], 'paper shown is', 'papers shown are')} recorded as used.")
                  for r in x["revised"]]
    lines += [t for _, _, t in sorted(timed, key=lambda t: (t[0], t[1]))]
    # validation papers moved to Development after they had been shown: one line per day (D3 of validation-reuse)
    mv = moved(project)
    by_day: dict[str, list[str]] = {}
    for e in mv:
        by_day.setdefault(d(e.get("at")), []).append(e["benchmark_id"])
    for day, ids in by_day.items():
        k = len(ids)
        lines.append(f"{_count(k, 'validation paper that had been shown was', 'validation papers that had been shown were')}"
                     f" moved to Development on {day} (former validation {'paper' if k == 1 else 'papers'}: "
                     f"{', '.join(ids)}).")
    plain = not (base or before_v5 or lines)
    if not gating:
        validated = False
        lines.append("Validation set not hidden (validation_after_freeze off).")
    final_at = st.get_meta(FINAL_KEY) or next((r["final_before"] for r in reversed(rows) if r["final_before"]), None)
    # what the report, the methods draft, the package and the Recall page add (not the coverage card): the final
    # mark's date, by source, and while the set is open the exact interval and the papers the query also found
    # (reading them while it is hidden would be a look)
    by_source = dict(sorted(Counter(b.source for b in vset).items()))
    shown = unlocked or not gating
    found = sum(1 for b in vset if retrieved_by_query(project, b.doi, b.title)) if shown else None
    ci = clopper_pearson(found, n_val) if shown and n_val else None
    n_qr = sum(1 for b in papers if b.set == "Validation" and b.source == "query_results")
    detail = [f"Uploads marked final on {d(final_at)}."] if final_at else []
    if by_source:
        detail.append(f"Where the validation papers came from: {source_counts(by_source)}.")
    if ci:
        detail.append(f"Recall on the validation set: {found} of {n_val}, 95% interval {ci[0]:.2f} to {ci[1]:.2f}.")
    if found:
        detail.append(f"{_count(found, 'validation paper was', 'validation papers were')} also found by the query.")
    if n_qr:
        detail.append(_count(n_qr, "validation paper came from the query results and is not counted.",
                             "validation papers came from the query results and are not counted."))
    # used and former validation papers: seen already, so whether the query found them is no look (D3, D5)
    readable = bool(st.scalar("SELECT COUNT(*) FROM raw_records"))
    used = used_papers(project, papers)
    used_found = [b.benchmark_id for b in used if retrieved_by_query(project, b.doi, b.title)] if readable else None
    if used and readable:
        detail.append(f"Recall on the {_count(len(used), 'used validation paper', 'used validation papers')} (not "
                      f"independent): {len(used_found)} of {len(used)}.")
    by_id = {b.benchmark_id: b for b in papers}
    former = [{"benchmark_id": e["benchmark_id"], "at": e.get("at"), "by": e.get("by"),
               "found_by_query": (bool(retrieved_by_query(project, e.get("doi"), by_id[e["benchmark_id"]].title
                                                          if e["benchmark_id"] in by_id else e.get("title_norm")))
                                  if readable else None)} for e in mv]
    if former and readable:
        detail.append(f"Former validation papers: {sum(1 for f in former if f['found_by_query'])} of {len(former)} "
                      "found by the query.")
    return {"used_validation": {"ids": [b.benchmark_id for b in used], "n": len(used),
                                "found": len(used_found) if used_found is not None else None},
            "former_validation": former,
            "moved": [{k: e.get(k) for k in ("benchmark_id", "at", "by")} for e in mv],
            "validated": validated, "validation_after_freeze": gating, "sentence": sentence, "lines": lines,
            "plain": plain, "peeked_at": st.get_meta("validation_peeked_at"), "peeked_before_v5": before_v5,
            "uploads_final_at": final_at, "first_unlock_at": counting[0]["at"] if counting else None,
            "n_unlocks": len(counting), "n_validation": n_val, "looks_before_freeze": before,
            "looks_after_unlock": [{"at": x["at"], "n": x["n"], **({"choice": x["choice"]} if x["choice"] else {})}
                                   for x in after],
            "unlocks_kept": sum(1 for r in rows if r.get("seen_choice") == "keep"),
            "choices": [{k: r[k] for k in ("unlock_no", "cause", "at", "from_version", "new_version", "n_shown",
                                           "seen_choice")} for r in asked],
            "revisions_after_look": [r for x in before for r in x["revised"]],
            "uploads_reopened_while_frozen": uploads, "freezes_in_place": freezes,
            "n_from_query_results": n_qr, "by_source": by_source, "n_found_by_query": found,
            "interval": list(ci) if ci else None, "detail": detail}


def validity_lines(note: dict) -> list[str]:
    """The whole note as the report, the methods draft, the package and the Recall page give it: the sentence, the
    look and choice lines, then by source, the interval and the counts."""
    return [BOTH_TEXT if note["sentence"] == BOTH_NOTE else note["sentence"], *note["lines"], *note["detail"]]


def _date(at: str | None) -> str:
    return (at or "")[:10]


def check(project: Project, scope: str = "platforms", include_validation: bool | None = None,
          seen_choice: str | None = None) -> dict:
    """scope: 'platforms' (each platform's raw records) or 'corpus' (deduplicated records).

    ``include_validation=None`` follows the protocol; ``True`` while the set is hidden is a logged look (:func:`look`),
    which before the first counting unlock needs ``seen_choice`` (raises :class:`SeenChoiceNeeded`, nothing recorded).
    The sets follow D1 and D3 (:func:`set_members`): Development (a former validation paper's row has ``former: True``,
    a Validation paper counted with Development ``was_development: True``), Validation only the papers that still
    count, the used validation papers under :data:`USED` (``used: True``) and papers from the query's results under
    :data:`NOT_COUNTED`, in neither set."""
    from .diagnostics import KNOWN_PAPERS_NOTE, clopper_pearson, recall_text

    papers = project.read_benchmark()
    unlocked = validation_unlocked(project)
    default = unlocked or not project.config.protocol.identification.validation_after_freeze
    if include_validation is None:
        include_validation = default
    peeked = bool(include_validation and not default)
    members = set_members(project, include_validation, papers)
    former = former_ids(project, papers)
    notice = look(project, seen_choice) if peeked else None
    bench = [(b.benchmark_id, b.title, b.doi) for _, b in members]
    st = project.store
    scopes: dict[str, list[CorpusRecord]] = {}
    if scope == "corpus":
        scopes["corpus"] = [CorpusRecord(str(r["record_id"]), r["title"] or "", r["doi"])
                            for r in st.query("SELECT record_id, title, doi FROM records")]
    else:
        for r in st.query("SELECT raw_id, platform, title, doi FROM raw_records"):
            scopes.setdefault(f"platform:{r['platform']}", []).append(CorpusRecord(str(r["raw_id"]), r["title"] or "", r["doi"]))
    thr = project.config.thresholds.jw_benchmark
    out: dict = {"validation_included": include_validation, "validation_unlocked": unlocked,
                 "validation_peeked": peeked, "notice": notice,
                 "used_validation": [b.benchmark_id for s_, b in members if s_ == USED],
                 "scopes": {}}
    now = utcnow()
    with st.tx() as c:
        for sc, corpus in scopes.items():
            matches = locate_benchmark(bench, corpus, jw_threshold=thr)
            rows = []
            for m in matches:
                c.execute("INSERT INTO benchmark_matches(benchmark_id,scope,record_ref,method,score,confirmed,checked_at) "
                          "VALUES(?,?,?,?,?,?,?) ON CONFLICT(benchmark_id,scope) DO UPDATE SET "
                          # a confirmation belongs to one match: a changed record or method takes the new check's
                          "confirmed=CASE WHEN record_ref IS excluded.record_ref AND method=excluded.method "
                          "THEN confirmed ELSE excluded.confirmed END, record_ref=excluded.record_ref, "
                          "method=excluded.method, score=excluded.score, checked_at=excluded.checked_at",
                          (m.benchmark_id, sc, int(m.record_id) if m.record_id else None, m.method, m.score,
                           0 if m.needs_confirmation else 1, now))
                rows.append({"benchmark_id": m.benchmark_id, "method": m.method, "score": round(m.score, 3),
                             "record_ref": m.record_id, "needs_confirmation": m.needs_confirmation})
            by_set: dict[str, list] = {}
            for s_, b in members:
                r = next(x for x in rows if x["benchmark_id"] == b.benchmark_id)
                by_set.setdefault(s_, []).append({**r, "title": b.title, **row_flags(s_, b, former)})
            out["scopes"][sc] = {}
            for s, lst in by_set.items():
                k = sum(1 for r in lst if r["method"] != "none")
                ci = clopper_pearson(k, len(lst)) if s != NOT_COUNTED else None
                out["scopes"][sc][s] = {"found": k, "n": len(lst), "rows": lst, "interval": list(ci) if ci else None,
                                        "text": recall_text(k, len(lst)) if s != NOT_COUNTED else f"{k} of {len(lst)}"}
    out["known_papers_note"] = KNOWN_PAPERS_NOTE
    out["note"] = validity_note(project)
    return out


def backfill_benchmark_abstracts(project: Project, min_words: int | None = None) -> int:
    """Copy abstracts from matched raw records into benchmark papers that have none (or a thin one)."""
    from ..core.text import word_count

    min_words = min_words or project.config.protocol.corpus.thin_words

    st = project.store
    n = 0
    for b in st.query("SELECT benchmark_id, abstract_words FROM benchmark_papers"):
        if (b["abstract_words"] or 0) >= min_words:
            continue
        best = None
        for m in st.query("SELECT scope, record_ref, confirmed FROM benchmark_matches WHERE benchmark_id=? AND record_ref IS NOT NULL",
                          (b["benchmark_id"],)):
            if not m["confirmed"]:
                continue
            table, col = ("records", "record_id") if m["scope"] == "corpus" else ("raw_records", "raw_id")
            r = st.one(f"SELECT abstract, year FROM {table} WHERE {col}=?", (m["record_ref"],))
            if r and r["abstract"] and (best is None or word_count(r["abstract"]) > word_count(best["abstract"])):
                best = r
        if best and word_count(best["abstract"]) >= min_words:
            st.execute("UPDATE benchmark_papers SET abstract=?, abstract_source='corpus', abstract_words=?, "
                       "year=COALESCE(year,?), fetched_at=? WHERE benchmark_id=?",
                       (best["abstract"], word_count(best["abstract"]), best["year"], utcnow(), b["benchmark_id"]))
            n += 1
    return n
