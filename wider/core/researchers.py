"""The researcher list: one entry per name WIDER records as an author, with a stable pseudonym (researcher-attribution).

An entry is *declared* when a person typed the name on the page or gave it with ``--by``/``--author``/``--reviewer``
(or it came with a review package), and *inferred* when WIDER took it from ``$WIDER_REVIEWER``, ``$USER``, an imported
label file or a project made before this list existed. WIDER cannot verify who made a change; declared only means a
researcher typed or confirmed the name.

Shared files (everything under ``exports/``, the share bundle, the replication package) go through one
:class:`Attributor`, which writes each name as the project's ``exports.attribution`` mode says. History and every
stored author keep the names as recorded; the list linking names and pseudonyms stays in the store.
"""
from __future__ import annotations

import getpass
import os
import re
from typing import TYPE_CHECKING, Any

from .provenance import utcnow
from .store import NOT_PEOPLE

if TYPE_CHECKING:
    from .project import Project

MODES = ("pseudonyms", "names", "none")
DECLARED_SOURCES = {"page", "option", "review package"}
SOURCE_WORDS = {
    "page": "typed on the page",
    "option": "given with --by, --author or --reviewer",
    "review package": "from a review package",
    "WIDER_REVIEWER": "from $WIDER_REVIEWER",
    "USER": "login name of this computer",
    "imported": "from an imported label file",
    "adjudication file": "from an adjudication file",
    "older version": "recorded before this version",
}
MODE_LINES = {
    "pseudonyms": "Names in shared files: pseudonyms (Researcher 1, Researcher 2 ...)",
    "names": "Names in shared files: declared names; inferred names stay pseudonyms",
    "none": "Names in shared files: none (no name and no pseudonym)",
}
REPRODUCE_LINES = {
    "pseudonyms": "Researcher names are replaced by pseudonyms (Researcher 1, Researcher 2 ...).",
    "names": "Declared researcher names are written as recorded; inferred names are replaced by pseudonyms.",
    "none": "Researcher names and pseudonyms are left out.",
}
NOT_VERIFIED = "WIDER cannot verify who made a change. Declared means a researcher typed or confirmed the name."
INFERRED_MARK = " (inferred, not confirmed)"
PLACEHOLDERS = ("import", "original")   # ids WIDER itself puts on imported labels; written "imported labels"
PASS_THROUGH = {"migration", "adjudication"}   # system authors and label sources that are not people


def _row(project: Project, name: str) -> dict | None:
    r = project.store.one("SELECT * FROM researchers WHERE name=?", (name,))
    return dict(r) if r else None


def register(project: Project, name: str, source: str) -> None:
    """Add ``name`` if it is new; upgrade an inferred entry to declared for a declared source; never downgrade.
    Ignores ``""``, ``migration`` and the import placeholders (D2)."""
    name = (name or "").strip()
    if name in NOT_PEOPLE or not source:
        return
    status = "declared" if source in DECLARED_SOURCES else "inferred"
    r = _row(project, name)
    if r is None:
        n = int(project.store.scalar("SELECT COALESCE(MAX(number),0) FROM researchers") or 0) + 1
        project.store.execute("INSERT INTO researchers(name,number,status,source,first_seen) VALUES(?,?,?,?,?)",
                              (name, n, status, source, utcnow()))
    elif status == "declared" and r["status"] == "inferred" and not r["joined_into"]:
        # a joined entry is written as its target; changing it here, outside the journal, would block undoing the join
        project.store.execute("UPDATE researchers SET status='declared', source=? WHERE name=?", (source, name))


def sign(project: Project, explicit: str | None = None) -> str | None:
    """The author of a row stored without a History change (comparisons, call reuse, result sets), registered like a
    journalled author so every name a shared file can hold has an entry; ``None`` when there is no name."""
    from .journal import author_with_source, check_name
    name, source = author_with_source(explicit)
    check_name(name)
    register(project, name, source)
    return name or None


def resolve(project: Project, name: str) -> dict | None:
    """The entry ``name`` stands for: the join target for a joined name (chains are one step deep)."""
    r, seen = _row(project, name), set()
    while r and r["joined_into"] and r["name"] not in seen:   # a redo of an older join can make a chain
        seen.add(r["name"])
        r = _row(project, r["joined_into"]) or r
    return r


def pseudonym(project: Project, name: str) -> str | None:
    r = resolve(project, name)
    return f"Researcher {r['number']}" if r else None


def is_inferred(project: Project, name: str) -> bool:
    if not name or name in NOT_PEOPLE:
        return False
    r = resolve(project, name)
    return bool(r) and r["status"] == "inferred"


def entries(project: Project) -> list[dict[str, Any]]:
    """Every entry by number; a joined entry shows its target's pseudonym and status."""
    rows = [dict(r) for r in project.store.query("SELECT * FROM researchers ORDER BY number")]
    by_name = {r["name"]: r for r in rows}
    out = []
    for r in rows:
        t = by_name.get(r["joined_into"] or "", r)
        out.append({"name": r["name"], "pseudonym": f"Researcher {t['number']}", "status": t["status"],
                    "source": SOURCE_WORDS.get(r["source"] or "", r["source"] or ""), "source_key": r["source"],
                    "first_seen": r["first_seen"], "joined_into": r["joined_into"]})
    return out


def confirm(project: Project, name: str, by: str | None = None) -> str:
    """Make an inferred entry declared, as one undoable History change. ``ValueError`` names why it cannot."""
    from .journal import recording
    r = _row(project, name)
    if r is None:
        raise ValueError(f"no researcher named {name!r}")
    if r["joined_into"]:
        raise ValueError(f"{name!r} is joined into {r['joined_into']!r}; confirm {r['joined_into']!r} instead")
    if r["status"] == "declared":
        raise ValueError(f"{name!r} is already declared")
    with recording(project, "researcher_confirm", f"researcher {name} confirmed", by) as rec:
        rec.rows("researchers", "name=?", [name])
        project.store.execute("UPDATE researchers SET status='declared' WHERE name=?", (name,))
    return f"{name} is declared ({pseudonym(project, name)})"


def join(project: Project, name: str, into: str, by: str | None = None) -> str:
    """Join ``name`` into ``into``: both then share ``into``'s pseudonym and status (D3). ``ValueError`` names the
    reason for each refusal."""
    from .journal import recording
    if name == into:
        raise ValueError("a name cannot be joined into itself")
    r, t = _row(project, name), _row(project, into)
    if r is None:
        raise ValueError(f"no researcher named {name!r}")
    if t is None:
        raise ValueError(f"no researcher named {into!r}")
    if t["joined_into"]:
        raise ValueError(f"{into!r} is itself joined into {t['joined_into']!r}; join into {t['joined_into']!r}")
    if r["joined_into"]:
        raise ValueError(f"{name!r} is already joined into {r['joined_into']!r}; undo that join first")
    other = project.store.scalar("SELECT name FROM researchers WHERE joined_into=? LIMIT 1", (name,))
    if other:
        raise ValueError(f"{other!r} is joined into {name!r}; join {name!r} into another name only after undoing that")
    with recording(project, "researcher_join", f"researcher {name} joined into {into}", by) as rec:
        rec.rows("researchers", "name=?", [name])
        project.store.execute("UPDATE researchers SET joined_into=? WHERE name=?", (into, name))
    return f"{name} is now {pseudonym(project, into)}, the same researcher as {into}"


def mode_of(project: Project) -> str:
    return project.config.exports.attribution


def set_attribution(project: Project, mode: str) -> str:
    """Save ``exports.attribution`` (journalled as a settings change). ``ValueError`` for an unknown mode, before
    anything is written."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose pseudonyms, names or none")
    project.config.exports.attribution = mode  # type: ignore[assignment]
    project.save()
    return MODE_LINES[mode]


# ------------------------------------------------------------------------------------------------- shared files
NAME_KEYS = {"author", "by", "accepted_by", "first_by", "choice_by", "answered_by", "reviewer", "labels_used",
             "terms_allowed_by", "allowed_by", "created_by", "reused_by"}
TEXT_KEYS = {"summary", "consequence", "reason", "text", "lines", "note"}
KEYED_KEYS = {"reviewers"}   # dicts keyed by a name (samples.*.progress.reviewers)
# the History line of a join ("researcher <name> joined into <name>", D3) names both sides; in a shared file it would
# link a pseudonym to the name it was joined into, so it is written without either name
JOIN_LINE = re.compile(r"researcher .+? joined into .+?(?= \(undone\)| \(change \d+\)|\s*\||$)", re.MULTILINE)
JOIN_SHARED = "two researcher names counted as one"
CONFIRM_LINE = re.compile(r"researcher (.+?) confirmed(?= \(undone\)| \(change \d+\)|\s*\||$)", re.MULTILINE)


def _pseudonym_like(name: str) -> bool:
    from .journal import PSEUDONYM_FORM
    return bool(PSEUDONYM_FORM.fullmatch(name or ""))
BIB_KEYS = {"title", "journal", "authors", "doi", "abstract", "title_norm", "terms", "query"}   # never names (D6)


def login_names() -> set[str]:
    """``$USER`` and the login name of this computer."""
    out = {(os.environ.get("USER") or "").strip()}
    try:
        out.add(getpass.getuser().strip())
    except Exception:  # noqa: BLE001 - no login name to check
        pass
    return out - {""}


class Attributor:
    """How names are written in one shared file write (D5). Built once per write; never writes to the store."""

    def __init__(self, project: Project, mode: str | None = None):
        self.project = project
        self.mode = mode or mode_of(project)
        rows = [dict(r) for r in project.store.query("SELECT * FROM researchers ORDER BY number")]
        self._rows = {r["name"]: r for r in rows}
        self._pattern: re.Pattern | None = None
        self._own_cache: dict[tuple, re.Pattern | None] = {}

    def _resolved(self, name: str) -> dict | None:
        r, seen = self._rows.get(name), set()
        while r and r["joined_into"] and r["name"] not in seen:   # a chain from a redo ends at one entry
            seen.add(r["name"])
            r = self._rows.get(r["joined_into"], r)
        return r

    def who(self, name: str | None) -> str:
        name = (name or "").strip()
        if not name:
            return "no author recorded"
        if name in PLACEHOLDERS:
            return "imported labels"
        if name in PASS_THROUGH:
            return name
        if self.mode == "none":
            return "not shown"
        r = self._resolved(name)
        if r is None:
            return "an unlisted researcher"
        if self.mode == "names" and r["status"] == "declared" and not _pseudonym_like(r["name"]):
            return r["name"]
        return f"Researcher {r['number']}"

    def hidden(self) -> dict[str, str]:
        """The names this mode does not write, each with its pseudonym. A joined name is always hidden."""
        out = {}
        for name in self._rows:
            if self.who(name) != name or self._rows[name]["joined_into"]:
                r = self._resolved(name)
                out[name] = f"Researcher {r['number']}" if r else name
        return out

    def person(self, name: str) -> str:
        """The entry a name stands for (a joined name gives its target), for counting people."""
        r = self._resolved(name)
        return r["name"] if r else name

    def is_inferred(self, name: str) -> bool:
        r = self._resolved(name)
        return bool(r) and r["status"] == "inferred"

    def unsigned(self) -> list[str]:
        """``$USER``, the login name and ``$WIDER_REVIEWER`` values that never signed a change."""
        vals = [*login_names(), os.environ.get("WIDER_REVIEWER") or ""]
        out = []
        for v in vals:
            v = v.strip()
            if v and v not in self._rows and v not in out:
                out.append(v)
        return out

    def _replacement(self, name: str) -> str:
        return "a researcher" if self.mode == "none" else self.who(name)

    @staticmethod
    def _compile(names: list[str]) -> re.Pattern | None:
        if not names:
            return None
        alts = sorted(set(names), key=len, reverse=True)
        return re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in alts) + r")(?!\w)")

    def _variants(self, name: str) -> list[str]:
        return [name] + ([name[:40]] if len(name) > 40 else [])

    def text(self, s: Any, own: tuple | list = ()) -> Any:
        """WIDER-written text with every hidden name replaced (whole word, case-sensitive): the row's own names of any
        length, other hidden names of 3+ characters, and each cut to 40 characters as ``config_diff`` cuts values."""
        if not isinstance(s, str) or not s:
            return s
        s = JOIN_LINE.sub(JOIN_SHARED, s)
        # the confirmed name, of any length (a short name is not replaced elsewhere in another row's text)
        s = CONFIRM_LINE.sub(lambda m: "a researcher name confirmed" if self.mode == "none"
                             else f"researcher {self.who(m.group(1))} confirmed", s)
        hidden = self.hidden()
        if self._pattern is None:
            names = [v for n in hidden if len(n) >= 3 for v in self._variants(n)]
            self._pattern = self._compile(names) or re.compile(r"(?!)")
        lookup = {v: n for n in hidden for v in self._variants(n)}
        own_names = tuple(n for n in own if isinstance(n, str) and n.strip() and n.strip() in hidden)
        if own_names:
            key = own_names
            if key not in self._own_cache:
                self._own_cache[key] = self._compile([v for n in own_names for v in self._variants(n.strip())])
            pat = self._own_cache[key]
            if pat is not None:
                s = pat.sub(lambda m: self._replacement(lookup.get(m.group(1), m.group(1))), s)
        return self._pattern.sub(lambda m: self._replacement(lookup.get(m.group(1), m.group(1))), s)

    def _keyed(self, d: dict) -> Any:
        if not all(isinstance(k, str) for k in d):
            return d
        if self.mode == "none":
            merged: dict[str, Any] = {}
            for k, v in d.items():
                r = self._resolved(k)
                key = r["name"] if r else k
                merged[key] = _add(merged[key], v) if key in merged else v
            return list(merged.values())
        out: dict[str, Any] = {}
        for k, v in d.items():
            key = self.who(k)
            out[key] = _add(out[key], v) if key in out else v
        return out

    def obj(self, obj: Any, name_keys: set | None = None, text_keys: set | None = None, all_text: bool = False) -> Any:
        """A copy of a JSON-like object with every name key through :meth:`who` and every text key through
        :meth:`text` (with the same dict's names as its own). ``all_text``: every string outside the bibliographic
        keys is WIDER-written text (the report)."""
        nk = NAME_KEYS if name_keys is None else name_keys
        tk = TEXT_KEYS if text_keys is None else text_keys

        def go(x: Any, own: list) -> Any:
            if isinstance(x, list):
                return [go(y, own) for y in x]
            if isinstance(x, str):
                return self.text(x, own) if all_text else x
            if not isinstance(x, dict):
                return x
            mine = [v for k, v in x.items() if k in nk and isinstance(v, str)]
            mine += [y for k, v in x.items() if k in nk and isinstance(v, list) for y in v if isinstance(y, str)]
            out = {}
            for k, v in x.items():
                if k in BIB_KEYS:
                    out[k] = v
                elif k in nk:
                    if isinstance(v, str) or (v is None and k == "author"):
                        out[k] = self.who(v)
                    elif isinstance(v, list):
                        out[k] = [self.who(y) if isinstance(y, str) else go(y, mine) for y in v]
                    else:
                        out[k] = go(v, mine)
                elif k in tk and isinstance(v, str):
                    out[k] = self.text(v, mine)
                elif k in tk and isinstance(v, list) and all(isinstance(y, str) for y in v):
                    out[k] = [self.text(y, mine) for y in v]
                elif k in KEYED_KEYS and isinstance(v, dict):
                    out[k] = self._keyed(v)
                else:
                    out[k] = go(v, mine if all_text else [])
            return out

        return go(obj, [])


def _add(a: Any, b: Any) -> Any:
    """Counts of two names that land on one pseudonym (a join) are added; dicts of counts key by key."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _add(out[k], v) if k in out else v
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return a + b
    return a


def attribute_obj(project: Project, obj: Any, mode: str | None = None, attributor: Attributor | None = None,
                  all_text: bool = False) -> Any:
    return (attributor or Attributor(project, mode)).obj(obj, all_text=all_text)


def attribute_report(project: Project, rep: dict, attributor: Attributor | None = None) -> dict:
    """``report.json`` as shared (D5): names through ``who``, every WIDER-written line through ``text``, and the
    interventions' ``authors`` (a bibliographic key elsewhere) mapped and de-duplicated."""
    att = attributor or Attributor(project)
    out = att.obj(rep, all_text=True)
    for src, k in zip((rep.get("interventions") or {}).get("by_kind") or [],
                      (out.get("interventions") or {}).get("by_kind") or []):
        k["authors"] = list(dict.fromkeys(att.who(a) for a in src.get("authors") or []))
    out["attribution"] = att.mode
    return out
