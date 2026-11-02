"""Ranking versions and "needs review" (ui-redesign D5).

A re-rank adds a version and never changes an earlier one: version 1 keeps the ranking's name, later versions are
stored as ``<name>@v<N>``, so every reader that kept a ranking's exact name (a sample's ``ranking_id``, the sampling
log) keeps reading the version it started from. Each version stores one input hash over what it was made from: the
texts its embeddings were made from (the embedding set's ``text_hash``, not the current texts), the thin flags,
``corpus.max_words``, ``corpus.thin_words``, the anchors version and the embedding set and model. A version that a
sample was drawn from (else the current one) "needs review" when that hash differs from the same hash over the
current inputs and the researcher did not keep it (meta ``review_kept:rank:<ranking_id>``). Rankings made before
input hashes were stored are never marked. Nothing here reruns anything or changes a stage's state.

Computing the current hash reads every record's text, so it is cached in memory per process, keyed by the project
folder, the records change counter (raised by SQLite triggers on ``records``) and the tracked settings; reading it never
writes to a store.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.project import Project

KEPT = "review_kept:rank:{rid}"      # meta: the current input hash a researcher kept, per ranking version
INPUTS = "rank_inputs:{rid}"         # meta: the parts behind a version's input hash, for the reason it names
ABLATION = "~ablation"               # an ablation whose inputs differ from the chosen ranking's is stored apart
VERSIONED = re.compile(r"^(?P<base>.+)@v(?P<version>\d+)$")
TEXTS_CHANGED = "the records or their abstracts changed since this ranking"
_TEXT_CACHE: dict[tuple, tuple[str, str]] = {}


def counter(project: Project) -> int:
    """The records change counter (raised by every insert, update and delete of a record)."""
    return int(project.store.get_meta("records_changes") or 0)


def current_texts(project: Project) -> tuple[str, str]:
    """``(text_hash, thin_hash)`` of the current corpus texts, as ``embed_corpus`` would hash them, cached in memory
    by folder, records counter and the two corpus settings."""
    from .corpus import corpus_texts, text_hash

    th = project.config.thresholds
    key = (str(project.root.resolve()), counter(project), th.max_words, th.thin_words)
    if key not in _TEXT_CACHE:
        ids, texts, thin = corpus_texts(project)
        _TEXT_CACHE[key] = (text_hash(ids, texts), _thin_hash(ids, thin))
    return _TEXT_CACHE[key]


def _thin_hash(ids: list[int], thin: list[bool]) -> str:
    h = hashlib.sha256()
    for i, t in zip(ids, thin):
        h.update(f"{i}:{int(t)};".encode())
    return h.hexdigest()


def current_anchors_version(project: Project) -> int | None:
    """The approved anchors version in force (approved or frozen), else None (no approved anchors: not tracked)."""
    from ..construct import artifacts

    row = artifacts.latest(project, "anchors", "approved")
    return row["version"] if row else None


def parts(project: Project, text_hash: str | None, set_name: str, model: str | None,
          anchors_version: int | None) -> dict[str, Any]:
    """The tracked inputs of a ranking: ``text_hash`` is the embedding set's (the texts the embeddings came from)."""
    th = project.config.thresholds
    return {"texts": text_hash, "thin": current_texts(project)[1], "max_words": th.max_words,
            "thin_words": th.thin_words, "anchors_version": anchors_version, "embedding": set_name, "model": model}


def input_hash(p: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()


def ranking_parts(project: Project, set_name: str, anchors_version: int) -> dict[str, Any]:
    """The inputs of a ranking made now from embedding set ``set_name`` and that anchors version."""
    row = project.store.one("SELECT text_hash, model FROM embedding_sets WHERE set_name=?", (set_name,))
    return parts(project, row["text_hash"] if row else None, set_name, row["model"] if row else None, anchors_version)


def current_parts(project: Project, anchors_version: int | None = None) -> dict[str, Any]:
    """The inputs a ranking made now from the current texts with the default embedding would have. While no anchors
    version is approved, a ranking is compared on its own anchors version (``anchors_version``): only approving other
    anchors marks it."""
    try:
        cfg = project.embedding(None)
        name, model = cfg.name, cfg.model
    except (KeyError, TypeError):
        name, model = project.config.default_embedding, None
    return parts(project, current_texts(project)[0], name, model, current_anchors_version(project) or anchors_version)


def reasons(stored: dict[str, Any] | None, now: dict[str, Any]) -> str:
    """Why a version needs review, in words; the records line covers texts and thin flags that changed on their own."""
    if not stored:
        return TEXTS_CHANGED
    out = []
    if (stored.get("texts") != now["texts"] and stored.get("max_words") == now["max_words"]) or \
            (stored.get("thin") != now["thin"] and stored.get("thin_words") == now["thin_words"]):
        out.append(TEXTS_CHANGED)
    if stored.get("anchors_version") != now["anchors_version"]:
        out.append(f"other anchors are approved (version {now['anchors_version']}) than this ranking used "
                   f"(version {stored.get('anchors_version')})")
    if (stored.get("embedding"), stored.get("model")) != (now["embedding"], now["model"]):
        out.append(f"the embedding model changed since this ranking ({stored.get('model')} to {now['model']})")
    for k in ("max_words", "thin_words"):
        if stored.get(k) != now[k]:
            out.append(f"corpus.{k} changed since this ranking ({stored.get(k)} to {now[k]})")
    return "; ".join(out) or TEXTS_CHANGED


# ----------------------------------------------------------------------------------------------- names and versions
def versions(project: Project, base: str) -> list[dict[str, Any]]:
    """Every version of base name ``base``, oldest first, each with the samples drawn from it."""
    st = project.store
    out = []
    for r in st.query("SELECT * FROM rankings WHERE base_name=? ORDER BY version", (base,)):
        d = dict(r)
        d["samples"] = [x["name"] for x in st.query("SELECT name FROM samples WHERE ranking_id=? ORDER BY sample_id",
                                                     (r["ranking_id"],))]
        out.append(d)
    return out


def resolve(project: Project, name: str | None) -> str | None:
    """The exact ranking name new work uses: the default ranking when ``name`` is None; for a base name, its newest
    version made by a current rank (else its newest version); ``NAME@vN`` names that version. Any other name is
    returned as given, for the reader to report as unknown."""
    st = project.store
    if not name:
        return st.get_meta("default_ranking")
    rows = st.query("SELECT name, version, current_run FROM rankings WHERE base_name=? ORDER BY version", (name,))
    if rows:
        current = [r for r in rows if r["current_run"]]
        return (current or rows)[-1]["name"]
    m = VERSIONED.match(name)
    if m and not st.one("SELECT 1 FROM rankings WHERE name=?", (name,)):
        r = st.one("SELECT name FROM rankings WHERE base_name=? AND version=?", (m["base"], int(m["version"])))
        if r:
            return r["name"]
    return name


def sample_ranking(project: Project, sample: str, logged: str | None = None):
    """The ranking version a sample was drawn from (``samples.ranking_id``), else the one its log names."""
    st = project.store
    r = st.one("SELECT r.* FROM rankings r JOIN samples s ON s.ranking_id=r.ranking_id WHERE s.name=?", (sample,))
    return r if r is not None or not logged else st.one("SELECT * FROM rankings WHERE name=?", (logged,))


def base_of(project: Project, name: str) -> str:
    """The base name of a stored ranking (its own name when it has none)."""
    r = project.store.one("SELECT base_name FROM rankings WHERE name=?", (name,))
    return (r["base_name"] if r and r["base_name"] else None) or name


def version_label(project: Project, name: str) -> str:
    """"version N" of a stored ranking."""
    r = project.store.one("SELECT version FROM rankings WHERE name=?", (name,))
    return f"version {r['version'] if r and r['version'] else 1}"


def store_version(c, base: str, ih: str, current: bool, row: dict[str, Any]) -> tuple[int, str, int, bool]:
    """Inside the ranking's transaction: reuse the version of ``base`` with input hash ``ih``, else add one. A current
    rank adds ``base@vN`` (or ``base`` for the first); an ablation (``current`` False) whose inputs differ from every
    version of an existing base name goes under ``base~ablation@vN``, never a version of ``base``. Returns
    ``(ranking_id, name, version, reused)``; ``row`` holds the other columns of a new version."""
    def rows_of(b):
        return c.execute("SELECT ranking_id, name, version, input_hash FROM rankings WHERE base_name=? "
                         "ORDER BY version", (b,)).fetchall()

    have = rows_of(base)
    target = base
    if have and not current and not any(r[3] == ih for r in have):
        target = base + ABLATION
        have = rows_of(target)
    same = [r for r in have if r[3] == ih]
    if same:
        rid, name, version = same[-1][0], same[-1][1], same[-1][2]
        if current:
            c.execute("UPDATE rankings SET current_run=1 WHERE ranking_id=?", (rid,))
        return rid, name, version, True
    version = (max(r[2] or 1 for r in have) + 1) if have else 1
    name = target if version == 1 and not c.execute("SELECT 1 FROM rankings WHERE name=?", (target,)).fetchone() \
        else f"{target}@v{version}"
    cur = c.execute(
        "INSERT INTO rankings(name,set_name,anchors_version,top_n,n,md5,created_at,note,base_name,version,input_hash,"
        "current_run,embedding_model) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, row["set_name"], row["anchors_version"], row["top_n"], row["n"], row["md5"], row["created_at"],
         row["note"], target, version, ih, int(current), row["embedding_model"]))
    return int(cur.lastrowid), name, version, False


def thin_words_effect(project: Project) -> str:
    """The line beside ``corpus.thin_words`` (page and ``wider protocol set``): changing it reruns nothing; it marks
    Ranking "needs review" only while a ranking made with input tracking exists (older rankings are never marked)."""
    st = project.store
    tracked = ("rankings" in st.tables() and "input_hash" in st.columns("rankings")
               and st.query("SELECT 1 FROM rankings WHERE input_hash IS NOT NULL LIMIT 1"))
    return ("Changing the thin-abstract words reruns nothing"
            + ('; it marks Ranking "needs review", because the ranking was made with the old value.' if tracked else "."))


# ----------------------------------------------------------------------------------------------------- needs review
def review_items(project: Project) -> list[dict[str, Any]]:
    """The ranking versions that need review: every version a sample was drawn from (else the current one) whose
    input hash differs from the current inputs and was not kept. Each item: ``ranking_id``, ``name``, ``base_name``,
    ``version``, ``samples``, ``reason``, ``current_hash``."""
    st = project.store
    if "rankings" not in st.tables() or "input_hash" not in st.columns("rankings"):
        return []
    rows = st.query("SELECT DISTINCT r.* FROM rankings r JOIN samples s ON s.ranking_id=r.ranking_id "
                    "ORDER BY r.ranking_id")
    if not rows:
        d = st.get_meta("default_ranking")
        rows = st.query("SELECT * FROM rankings WHERE name=?", (d,)) if d else []
    rows = [r for r in rows if r["input_hash"]]
    if not rows:
        return []
    out = []
    for r in rows:
        now = current_parts(project, r["anchors_version"])
        ch = input_hash(now)
        if r["input_hash"] == ch or st.get_meta(KEPT.format(rid=r["ranking_id"])) == ch:
            continue
        stored = st.get_meta(INPUTS.format(rid=r["ranking_id"]))
        samples = [x["name"] for x in st.query("SELECT name FROM samples WHERE ranking_id=? ORDER BY sample_id",
                                               (r["ranking_id"],))]
        out.append({"ranking_id": r["ranking_id"], "name": r["name"], "base_name": r["base_name"] or r["name"],
                    "version": r["version"] or 1, "samples": samples, "current_hash": ch,
                    "reason": reasons(json.loads(stored) if stored else None, now)})
    return out


def review_text(items: list[dict[str, Any]]) -> str | None:
    """The stage's ``needs_review`` words: the reason of one version, else each version with its samples."""
    if not items:
        return None
    if len(items) == 1:
        return items[0]["reason"]
    return "; ".join(f"version {i['version']}" + (f" ({', '.join(i['samples'])})" if i["samples"] else "") +
                     f": {i['reason']}" for i in items)



def page_review(project: Project) -> list[dict[str, Any]]:
    """:func:`review_items` for the Ranking page and `wider rank show`: each sample with its kind and whether it has
    labels, which decide the actions its box offers (a labelled development sample cannot be drawn afresh)."""
    st = project.store
    out = []
    for it in review_items(project):
        rows = st.query("SELECT s.name, s.kind, EXISTS(SELECT 1 FROM labels l WHERE l.sample_id=s.sample_id) labelled "
                        "FROM samples s WHERE s.ranking_id=? ORDER BY s.sample_id", (it["ranking_id"],))
        out.append({**it, "samples": [{"name": r["name"], "kind": r["kind"], "labelled": bool(r["labelled"])}
                                      for r in rows]})
    return out

# ------------------------------------------------------------------------------------------------- keep on purpose
NOTHING_TO_KEEP = "the ranking does not need review"
KEPT_SUMMARY = "Kept the ranking after the records changed"


def keep(project: Project, version: int | None = None, author: str | None = None) -> dict[str, Any]:
    """Keep a ranking version that needs review: a signed, undoable History change (kind ``ranking_kept``) storing
    the current input hash as kept for that version. Nothing is rerun. Refused when nothing needs review, and, without
    ``version``, when several versions do (naming them and their samples)."""
    from ..core import journal

    items = review_items(project)
    if not items:
        raise ValueError(NOTHING_TO_KEEP)
    if version is None:
        if len(items) > 1:
            named = "; ".join(f"version {i['version']}" + (f" ({', '.join(i['samples'])})" if i["samples"] else "")
                              for i in items)
            raise ValueError(f"several ranking versions need review: {named}. Choose one with --version")
        it = items[0]
    else:
        it = next((i for i in items if i["version"] == version), None)
        if it is None:
            raise ValueError(f"version {version} of the ranking does not need review; versions that do: "
                             + ", ".join(str(i["version"]) for i in items))
    key = KEPT.format(rid=it["ranking_id"])
    summary = f"{KEPT_SUMMARY} (version {it['version']} of {it['base_name']})"
    with journal.recording(project, "ranking_kept", summary, author) as rec:
        rec.rows("meta", "key=?", [key])
        project.store.set_meta(key, it["current_hash"])
    return {"version": it["version"], "name": it["name"], "samples": it["samples"], "reason": it["reason"],
            "change_id": rec.change_id, "message": f"kept version {it['version']} of the ranking; nothing was rerun"}


def kept_sentences(project: Project, who) -> list[str]:
    """The methods draft's sentence for each ranking version the samples were drawn from that was kept after its
    records or abstracts changed (the keep still in force), with its date and who kept it, written by ``who``."""
    from datetime import datetime

    st = project.store
    out = []
    rids = [r[0] for r in st.query("SELECT DISTINCT ranking_id FROM samples WHERE ranking_id IS NOT NULL "
                                   "ORDER BY ranking_id")]
    kept = st.query("SELECT at, author, items FROM history WHERE kind='ranking_kept' ORDER BY change_id DESC")
    for rid in rids:
        key = KEPT.format(rid=rid)
        if st.get_meta(key) is None:   # never kept, or the keep was undone
            continue
        ch = next((c for c in kept if any(it.get("params") == [key] for it in json.loads(c["items"] or "[]"))), None)
        if ch is None:
            continue
        day = datetime.fromisoformat(ch["at"].replace("Z", "+00:00"))
        out.append(f"The ranking was kept after the records or their abstracts changed (kept on "
                   f"{day.day} {day:%b %Y} by {who(ch['author'])}).")
    return out
