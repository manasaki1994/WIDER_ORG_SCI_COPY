"""The methods document (methods-explainer D1–D7): why WIDER works the way it does, one Markdown file per topic in
three layers ("In short", "Why", "For auditors") plus a glossary. ``sections()`` feeds the Methods page, ``wider
guide methods`` and guide-assistant from one place. ``{default:section.key}`` in a text is filled from the shipped
protocol when the file is read, so a changed default needs no text edit.

``review.yaml`` beside the files keeps, per topic, the code it describes (``sources``), a fingerprint of that code and
of its settings' defaults, and the owner's review status. Only the gate and ``scripts/methods_review.py`` compute
fingerprints (D6). The files are read as they are (guide-assistant D22): the cache is keyed by :func:`corpus_key`, so a
saved edit is used by the next call; a reviewed text whose file has changed since reads as drafted with ``edited``; a
topic file or glossary entry that cannot be read is left out and named by :func:`problems`. Nothing here reads a
project."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).parent
BASE = HERE.parent.parent.parent          # the folder that holds ``wider/``: sources are relative to it
TOPIC_ORDER = ("overview", "query-construction", "development-validation-split", "deduplication", "ranking",
               "development-sample", "stratified-sampling", "recall-estimate", "recall-target", "llm-screening",
               "labels-and-agreement", "blinding", "consent-and-privacy", "provenance-and-reporting")
LAYERS = (("short", "In short"), ("why", "Why"), ("auditors", "For auditors"))
KEYS = ("id", "title", "context", "settings", "artifacts", "pages")
GLOSSARY = "glossary"                     # reserved: the glossary's id, never a topic's
REVIEW = "review.yaml"
NO_TOPIC = "there is no such methods topic: {topic}"
_DEFAULT = re.compile(r"\{default:([^}]*)\}")


@dataclass(frozen=True)
class Section:
    """One retrievable piece (guide-assistant D3): a layer of a topic or a glossary term."""
    id: str
    kind: str                 # "guide" | "methods" | "glossary"
    topic: str | None
    layer: str | None         # "short" | "why" | "auditors" | None
    title: str
    context: str
    text: str
    settings: tuple[str, ...]
    artifacts: tuple[str, ...]
    pages: tuple[str, ...]
    anchor: str


# ------------------------------------------------------------------------------------------------ reading
def _root(root: Path | None) -> Path:
    return Path(root) if root is not None else HERE


def default_text(value: Any) -> str:
    """A protocol default in words: ``None`` is "not set", a flag "on"/"off", a list joined by commas."""
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list | tuple):
        return ", ".join(default_text(v) for v in value)
    return str(value)


@lru_cache(maxsize=1)
def _defaults() -> dict[str, Any]:
    from ...core.protocol import ReviewProtocol
    dumped = ReviewProtocol().model_dump(mode="json")
    return {f"{s}.{k}": v for s, sub in dumped.items() for k, v in sub.items()}


def _fill(text: str, topic_id: str) -> str:
    def one(m: re.Match) -> str:
        key = m[1].strip()
        if key not in _defaults():
            raise ValueError(f"methods topic {topic_id}: {{default:{key}}} names no protocol setting")
        return default_text(_defaults()[key])
    return _DEFAULT.sub(one, text)


def _split(path: Path) -> tuple[dict, str]:
    """Front matter and body of a topic file; ``ValueError`` naming the file when the front matter is missing."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n") or "\n---\n" not in raw[4:]:
        raise ValueError(f"methods topic {path.stem}: no front matter between --- lines")
    head, body = raw[4:].split("\n---\n", 1)
    meta = yaml.safe_load(head) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"methods topic {path.stem}: the front matter is not a mapping")
    return meta, body


def _layers(body: str, topic_id: str) -> list[dict]:
    """The three layers in order; ``ValueError`` naming the topic when a heading is missing, extra or out of order."""
    found: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        if line.startswith("## "):
            found.append((line[3:].strip(), []))
        elif line.startswith("#"):
            raise ValueError(f"methods topic {topic_id}: only the headings In short, Why and For auditors are allowed")
        elif found:
            found[-1][1].append(line)
    headings = [h for h, _ in found]
    if headings != [h for _, h in LAYERS]:
        raise ValueError(f"methods topic {topic_id}: the headings must be In short, Why, For auditors in that order "
                         f"(found {', '.join(headings) or 'none'})")
    out = []
    for (layer, heading), (_, lines) in zip(LAYERS, found, strict=True):
        text = "\n".join(lines).strip()
        if not text:
            raise ValueError(f"methods topic {topic_id}: the layer {heading} has no text")
        out.append({"layer": layer, "heading": heading, "text": text})
    return out


def _review(root: Path) -> dict:
    path = root / REVIEW
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return data or {}


def corpus_key(root: Path | None = None) -> str:
    """sha256 over the name and bytes of every ``*.md`` and ``review.yaml`` in the folder (guide-assistant D22): the
    cache key of the loader, so an edit saved to any file is read by the next call without a restart."""
    root = _root(root)
    h = hashlib.sha256()
    for path in sorted([*root.glob("*.md"), root / REVIEW]):
        if path.is_file():
            h.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def _status(entry: dict | None, path: Path) -> dict:
    """``{status, date}``; a topic marked reviewed whose file differs from the reviewed text shows as drafted with its
    review date and ``edited: true`` (the key is present only then); a stamped, never-reviewed one stays drafted."""
    entry = entry or {}
    when = entry.get("date")
    status = entry.get("status", "drafted")
    out: dict[str, Any] = {"status": status, "date": str(when) if when else None}
    if status == "reviewed" and entry.get("text") != hashlib.sha256(path.read_bytes()).hexdigest():
        out.update(status="drafted", edited=True)
    return out


def _topic(path: Path, review: dict) -> dict:
    meta, body = _split(path)
    tid = meta.get("id")
    if tid != path.stem:
        raise ValueError(f"methods topic {path.stem}: its id must equal the file name (found {tid!r})")
    layers = _layers(body, tid)
    for layer in layers:
        layer["text"] = _fill(layer["text"], tid)
    return {"id": tid, "title": str(meta.get("title", "")), "context": _fill(str(meta.get("context", "")), tid),
            "settings": list(meta.get("settings") or []), "artifacts": list(meta.get("artifacts") or []),
            "pages": list(meta.get("pages") or []),
            "review": _status((review.get("topics") or {}).get(tid), path), "layers": layers, "_meta": meta}


@lru_cache(maxsize=8)
def _load(root_s: str, key: str) -> tuple[list[dict], dict, tuple[tuple[str, str], ...]]:
    """The topics in order, the review data and the ``(file, problem)`` of each topic file that cannot be read, which
    is left out (a half-saved file must not break the page or a question, D22). ``key`` is :func:`corpus_key`."""
    root = Path(root_s)
    review = _review(root)
    found, bad = {}, []
    for path in sorted(root.glob("*.md")):
        if path.stem == GLOSSARY:
            continue
        try:
            t = _topic(path, review)
        except (ValueError, yaml.YAMLError, UnicodeDecodeError) as e:
            bad.append((path.name, str(e).splitlines()[0]))
            continue
        found[t["id"]] = t
    order = [t for t in TOPIC_ORDER if t in found] + sorted(t for t in found if t not in TOPIC_ORDER)
    return [found[t] for t in order], review, tuple(bad)


def _topics(root: Path | None) -> list[dict]:
    root = _root(root)
    return _load(str(root), corpus_key(root))[0]


def problems(root: Path | None = None) -> list[tuple[str, str]]:
    """``[(file, problem)]`` of the topic files and glossary entries that cannot be read and are left out."""
    root = _root(root)
    key = corpus_key(root)
    return list(_load(str(root), key)[2]) + list(_glossary(str(root), key)[2])


def problem_line(file: str, problem: str) -> str:
    """The sentence the Methods page, ``wider guide methods`` and Ask WIDER show for one problem (D22)."""
    return f"WIDER's documents: {file} cannot be read ({problem}); it is left out until it is fixed."


def problem_lines(root: Path | None = None) -> list[dict]:
    """``[{file, text}]``: each problem with its sentence, for ``GET /api/methods`` and the CLI's JSON."""
    return [{"file": f, "text": problem_line(f, p)} for f, p in problems(root)]


def bad_topic(topic_id: str | None, root: Path | None = None) -> str | None:
    """The problem sentence when ``<topic_id>.md`` exists but cannot be read, else None: the CLI exits 1 and the route
    answers 404 with it instead of "there is no such methods topic"."""
    return next((p["text"] for p in problem_lines(root) if p["file"] == f"{topic_id}.md"), None)


def _public(t: dict, layers: bool) -> dict:
    keep = ("id", "title", "context", "settings", "artifacts", "pages", "review") + (("layers",) if layers else ())
    return {k: (list(t[k]) if isinstance(t[k], list) else dict(t[k]) if isinstance(t[k], dict) else t[k]) for k in keep}


def topics(root: Path | None = None) -> list[dict]:
    """``[{id, title, context, settings, artifacts, pages, review: {status, date}}]`` in ``TOPIC_ORDER``."""
    return [_public(t, layers=False) for t in _topics(root)]


def topic(topic_id: str, root: Path | None = None) -> dict:
    """One topic with ``layers: [{layer, heading, text}]``; ``KeyError`` for an unknown id."""
    for t in _topics(root):
        if t["id"] == topic_id:
            out = _public(t, layers=True)
            out["layers"] = [dict(x) for x in t["layers"]]
            return out
    raise KeyError(topic_id)


def raw_meta(topic_id: str, root: Path | None = None) -> dict:
    """The front matter as written (for the format test)."""
    for t in _topics(root):
        if t["id"] == topic_id:
            return dict(t["_meta"])
    raise KeyError(topic_id)


def slug(heading: str) -> tuple[str, str, str | None]:
    """``(slug, term, synonym)``: the heading lower-cased, a parenthesised part removed and returned as a synonym,
    every run of other characters than a-z0-9 turned into one ``-`` (D4)."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", heading.strip())
    term, extra = (m[1].strip(), m[2].strip()) if m else (heading.strip(), None)
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-"), term, extra


@lru_cache(maxsize=8)
def _glossary(root_s: str, key: str) -> tuple[list[dict], dict, tuple[tuple[str, str], ...]]:
    """The entries, the glossary's review and the ``(file, problem)`` of each entry that cannot be read (a heading
    with no letter or digit, so no slug), which is left out with its lines. ``key`` is :func:`corpus_key`."""
    root = Path(root_s)
    path = root / f"{GLOSSARY}.md"
    if not path.exists():
        return [], {"status": "drafted", "date": None}, ()
    status = _status(_review(root).get(GLOSSARY), path)
    entries: list[dict] = []
    bad: list[tuple[str, str]] = []
    skipping = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            s, term, extra = slug(line[3:])
            skipping = not s
            if skipping:
                bad.append((path.name, f"the heading {line[3:].strip()!r} names no term"))
                continue
            entries.append({"term": term, "slug": s, "synonyms": [extra] if extra else [], "definition": [],
                            "_syn_line": False})
        elif not entries or skipping:
            continue
        elif line.startswith("synonyms:"):
            entries[-1]["synonyms"] += [x.strip() for x in line[len("synonyms:"):].split(";") if x.strip()]
            entries[-1]["_syn_line"] = True
        elif line.strip():
            entries[-1]["definition"].append(line.strip())
    for e in entries:
        e["definition"] = " ".join(e["definition"])
    return entries, status, tuple(bad)


def _gloss(root: Path | None) -> tuple[list[dict], dict, tuple[tuple[str, str], ...]]:
    root = _root(root)
    return _glossary(str(root), corpus_key(root))


def glossary(root: Path | None = None) -> list[dict]:
    """``[{term, slug, synonyms, definition}]`` in file order."""
    return [{k: (list(v) if isinstance(v, list) else v) for k, v in e.items() if not k.startswith("_")}
            for e in _gloss(root)[0]]


def glossary_review(root: Path | None = None) -> dict:
    """The glossary's review ``{status, date}``."""
    return dict(_gloss(root)[1])


def glossary_has_synonym_line(root: Path | None = None) -> dict[str, bool]:
    """Per slug, whether the entry wrote its ``synonyms:`` line (the format test)."""
    return {e["slug"]: e["_syn_line"] for e in _gloss(root)[0]}


def sections(root: Path | None = None) -> list[Section]:
    """Three sections per topic (``methods/<topic>/<layer>``) and one per glossary term (``glossary/<slug>``)."""
    out = []
    for t in _topics(root):
        for layer in t["layers"]:
            out.append(Section(id=f"methods/{t['id']}/{layer['layer']}", kind="methods", topic=t["id"],
                               layer=layer["layer"], title=t["title"], context=t["context"], text=layer["text"],
                               settings=tuple(t["settings"]), artifacts=tuple(t["artifacts"]), pages=tuple(t["pages"]),
                               anchor=f"#methods/{t['id']}/{layer['layer']}"))
    for e in glossary(root):
        text = f"Also called: {'; '.join(e['synonyms'])}. {e['definition']}" if e["synonyms"] else e["definition"]
        out.append(Section(id=f"glossary/{e['slug']}", kind="glossary", topic=None, layer=None, title=e["term"],
                           context="", text=text, settings=(), artifacts=(), pages=(),
                           anchor=f"#methods/glossary/{e['slug']}"))
    return out


def for_page(page: str, root: Path | None = None) -> list[dict]:
    """``[{id, title}]`` of the topics whose ``pages`` include ``page`` (the guide's "Why WIDER works this way")."""
    return [{"id": t["id"], "title": t["title"]} for t in topics(root) if page in t["pages"]]


def review_line(review: dict) -> str:
    """The sentence under a title: reviewed with its date, edited since that review (guide-assistant D22), or
    drafted (D8)."""
    if review.get("status") == "reviewed" and review.get("date"):
        return f"Reviewed by the author on {review['date']}."
    if review.get("edited") and review.get("date"):
        return f"Edited since the author's review on {review['date']}; not yet reviewed again."
    return "Drafted; not yet reviewed by the author."


def methods_lines(topic_id: str | None = None, root: Path | None = None) -> list[str]:
    """The text of ``wider guide methods [<topic>]``: the topic list and the glossary terms, one topic's three layers,
    or the whole glossary; ``KeyError`` for an unknown topic."""
    if topic_id == GLOSSARY:
        out = ["Glossary", review_line(glossary_review(root)), ""]
        for e in glossary(root):
            out.append(e["term"] + (f" (also: {'; '.join(e['synonyms'])})" if e["synonyms"] else ""))
            out += [f"  {e['definition']}", ""]
        return out[:-1]
    if topic_id:
        t = topic(topic_id, root)
        out = [t["title"], t["context"], review_line(t["review"])]
        for layer in t["layers"]:
            out += ["", layer["heading"], layer["text"]]
        return out
    out = [p["text"] for p in problem_lines(root)]
    out += ["Why WIDER works this way: wider guide methods <topic> prints one topic.", ""]
    for t in topics(root):
        out += [f"{t['id']}  {t['title']} — {t['context']}", f"  {review_line(t['review'])}"]
    out += ["", f"Glossary (wider guide methods {GLOSSARY}): " + ", ".join(e["term"] for e in glossary(root)),
            f"  {review_line(glossary_review(root))}"]
    return out


# -------------------------------------------------------------------------------------- staying current (D6)
def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _named(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name == name:
            return node
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] \
            if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return node
    return None


def resolve(ref: str, base: Path | None = None) -> str | None:
    """Why ``path`` or ``path:name`` does not resolve, or ``None`` when it does."""
    rel, _, name = ref.partition(":")
    path = (base or BASE) / rel
    if not path.is_file():
        return f"{rel} does not exist"
    if name:
        if path.suffix != ".py":
            return f"{ref}: only a .py source can name a function"
        if _named(ast.parse(path.read_text(encoding="utf-8")), name) is None:
            return f"{rel} has no top-level {name}"
    return None


def normalised(ref: str, base: Path | None = None) -> str:
    """The code a fingerprint covers: ``ast.unparse`` without docstrings (of the module or the named top-level
    function, class or variable); a ``.yaml`` source as its parsed data with sorted keys."""
    rel, _, name = ref.partition(":")
    path = (base or BASE) / rel
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return json.dumps(yaml.safe_load(text), sort_keys=True, default=str)
    tree = _strip_docstrings(ast.parse(text))
    node = _named(tree, name) if name else tree   # type: ignore[arg-type]
    if node is None:
        raise ValueError(f"{rel} has no top-level {name}")
    return ast.unparse(node)


def fingerprint(topic_id: str, root: Path | None = None, base: Path | None = None) -> str:
    """sha256 over each source's normalised code, then the topic's settings with their defaults as JSON."""
    root = _root(root)
    entry = (_review(root).get("topics") or {}).get(topic_id) or {}
    meta, _ = _split(root / f"{topic_id}.md")
    h = hashlib.sha256()
    for ref in entry.get("sources") or []:
        h.update(ref.encode() + b"\0" + normalised(ref, base).encode() + b"\0")
    defaults = _defaults()
    h.update(json.dumps({k: defaults.get(k) for k in meta.get("settings") or []}, sort_keys=True).encode())
    return h.hexdigest()


def text_hash(topic_id: str, root: Path | None = None) -> str:
    """sha256 of the topic file as written (a re-stamp on a new Python minor keeps the review when it is unchanged)."""
    name = f"{topic_id}.md"
    return hashlib.sha256((_root(root) / name).read_bytes()).hexdigest()


def python_minor() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def other_minor(root: Path | None = None) -> str | None:
    """A message when the fingerprints were stored on another Python minor than this one (D6), else ``None``."""
    stored = str(_review(_root(root)).get("python") or "")
    if stored and stored != python_minor():
        return (f"methods fingerprints were computed on Python {stored}; this is Python {python_minor()}, "
                "so the fingerprint check is skipped here (local CI runs it)")
    return None


def stale(root: Path | None = None, base: Path | None = None) -> list[str]:
    """Topics whose stored fingerprint differs from the code (or that have none)."""
    root = _root(root)
    stored = _review(root).get("topics") or {}
    ids = [t["id"] for t in _topics(root)]
    return [t for t in ids if (stored.get(t) or {}).get("fingerprint") != fingerprint(t, root, base)]


def _write_review(root: Path, data: dict) -> None:
    (root / REVIEW).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
    _load.cache_clear()
    _glossary.cache_clear()


def stamp(topic_id: str, root: Path | None = None, base: Path | None = None, today: date | None = None,
          new_minor: bool | None = None) -> dict:
    """Write the topic's (or the glossary's) new fingerprint with status ``drafted`` and today's date, and the running
    Python minor. On a new minor with the file unchanged, a reviewed topic or glossary keeps its review (D6);
    ``new_minor`` is passed by ``stamp_all``, which works it out once before the first stamp rewrites ``python``."""
    root = _root(root)
    data = _review(root)
    when = (today or date.today()).isoformat()
    if new_minor is None:
        new_minor = str(data.get("python") or "") not in ("", python_minor())
    if topic_id == GLOSSARY:
        entry = data.get(GLOSSARY) or {}
        data[GLOSSARY] = entry
    else:
        if topic_id not in [t["id"] for t in _topics(root)]:
            raise KeyError(topic_id)
        topics_ = data.setdefault("topics", {})
        entry = topics_.setdefault(topic_id, {"sources": []})
        entry["fingerprint"] = fingerprint(topic_id, root, base)
    keep = new_minor and entry.get("status") == "reviewed" and entry.get("text") == text_hash(topic_id, root)
    entry["text"] = text_hash(topic_id, root)
    if not keep:
        entry["status"], entry["date"] = "drafted", when
        entry.pop("answer", None)
    data["python"] = python_minor()
    _write_review(root, data)
    return data


def stamp_all(root: Path | None = None, base: Path | None = None, today: date | None = None) -> list[tuple[str, str]]:
    """``stamp`` every topic and the glossary; ``[(id, status)]``. A new minor is decided once, from the stored one."""
    root = _root(root)
    new_minor = str(_review(root).get("python") or "") not in ("", python_minor())
    out = []
    for tid in [t["id"] for t in _topics(root)] + [GLOSSARY]:
        data = stamp(tid, root, base, today, new_minor=new_minor)
        entry = data[GLOSSARY] if tid == GLOSSARY else data["topics"][tid]
        out.append((tid, entry["status"]))
    return out


def text_changed(root: Path | None = None) -> list[str]:
    """Topics (and ``glossary``) marked reviewed whose file differs from the text the owner reviewed."""
    root = _root(root)
    data = _review(root)
    entries = dict(data.get("topics") or {})
    if (root / f"{GLOSSARY}.md").exists():
        entries[GLOSSARY] = data.get(GLOSSARY) or {}
    return [t for t, e in entries.items()
            if (e or {}).get("status") == "reviewed" and (e or {}).get("text") != text_hash(t, root)]


def approve(topic_id: str, answer: str, root: Path | None = None, base: Path | None = None,
            today: date | None = None) -> dict:
    """Record the owner's review (``reviewed``, the date, the dashboard answer id); ``ValueError`` when the topic's
    fingerprint is stale, or when its file changed after the stamp (stamp it after updating the text first)."""
    root = _root(root)
    data = _review(root)
    when = (today or date.today()).isoformat()
    if topic_id == GLOSSARY:
        entry = data.get(GLOSSARY)
    else:
        entry = (data.get("topics") or {}).get(topic_id)
        if entry is None:
            raise KeyError(topic_id)
        if not other_minor(root) and topic_id in stale(root, base):
            raise ValueError(f"methods topic {topic_id} is stale: update its text, then run "
                             f"python3 scripts/methods_review.py stamp {topic_id}, before approving")
    if not entry or entry.get("text") != text_hash(topic_id, root):
        raise ValueError(f"methods {topic_id}: the file differs from the stamped text, so the owner has not reviewed "
                         f"it; run python3 scripts/methods_review.py stamp {topic_id} and ask again")
    entry.update(status="reviewed", date=when, answer=answer)
    _write_review(root, data)
    return data
