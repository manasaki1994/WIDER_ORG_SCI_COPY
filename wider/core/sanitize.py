"""Shared outputs without home paths or credentials (design Decision 21).

:func:`sanitized_project_yaml` is the ``project.yaml`` a share bundle carries: home-folder paths as ``~/...``, paths
inside the project relative, other absolute paths cut to ``<path>/<last part>``; header and ``extra.env`` values stay
references (``{env:NAME}``); consent records lose ``allowed_by``. Every other setting is kept. :func:`sanitize_json`
does the same to a run folder's JSON copy (the original is never changed).

:func:`scan_for_secrets` reads every text file of an output before it is finished and reports, per finding, the file,
the line and the kind, never the value: the values of the environment variables the project names (when set and at
least 8 characters, compared in memory only), common key shapes and the home-folder path. :func:`scan_for_names`
then looks for the researcher names the attribution setting hides (researcher-attribution D6).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import yaml

ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
KEY_SHAPES = [
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
]
TEXT_SUFFIXES = {".json", ".jsonl", ".csv", ".md", ".yaml", ".yml", ".txt", ".ris", ".bib", ".tsv", ".html", ".svg"}
MIN_SECRET_LEN = 8


@dataclass
class Finding:
    file: str
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.kind}"


class SecretFound(ValueError):
    """A shared output held something that looks like a credential or a home path; the output was deleted."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        shown = "; ".join(str(f) for f in findings[:10]) + (f"; and {len(findings) - 10} more" if len(findings) > 10 else "")
        super().__init__(f"refused: the output would contain secrets or private paths ({shown}); nothing was written")


def _home() -> str:
    return str(Path.home())


_ABS_PATH = re.compile(r"(?<![\w.~:/])/(?:[^\s/\"',\]]+/)+([^\s/\"',\]]*)")


def portable_text(text: str, home: str | None = None) -> str:
    """Free text (a History summary) with every home-folder path written from ``~`` and every other absolute path cut
    to ``<path>/<last part>`` (the rules of :func:`portable_path`)."""
    home = home or _home()
    if not text:
        return text
    if len(home) > 1:
        text = text.replace(home, "~")   # every occurrence, as the scan of a shared file looks for any
    return _ABS_PATH.sub(lambda m: "<path>/" + m.group(1), text)


def portable_path(value: str, project_root: Path | None = None, home: str | None = None) -> str:
    """A path as a shared file shows it (inside the project: relative; home: ``~/...``; else ``<path>/<last>``)."""
    home = home or _home()
    if project_root is not None:
        root = str(project_root)
        if value == root:
            return "."
        if value.startswith(root + os.sep):
            return value[len(root) + 1:]
    if value == home:
        return "~"
    if value.startswith(home + os.sep):
        return "~" + value[len(home):]
    if os.path.isabs(value) and len(value) > 1:
        return "<path>/" + os.path.basename(value.rstrip(os.sep))
    return value


def _clean(obj: Any, project_root: Path | None, home: str, dropped: list[str], path: str = "") -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if k == "allowed_by":   # consent records keep what was allowed, not who
                dropped.append(here)
                continue
            out[k] = _clean(v, project_root, home, dropped, here)
        return out
    if isinstance(obj, list):
        return [_clean(v, project_root, home, dropped, f"{path}[{i}]") for i, v in enumerate(obj)]
    if isinstance(obj, str):
        if home in obj or (project_root is not None and str(project_root) in obj):
            # paths inside a longer string (an argv entry, a command line)
            s = obj
            if project_root is not None:
                s = s.replace(str(project_root) + os.sep, "").replace(str(project_root), ".")
            return s.replace(home, "~")
        if os.path.isabs(obj) and "\n" not in obj and " " not in obj.strip() and len(obj) > 1 and obj.count("/") > 1:
            return portable_path(obj, project_root, home)
        return obj
    return obj


def sanitize_json(data: Any, project_root: Path | None = None) -> tuple[Any, list[str]]:
    """``(copy, dropped fields)`` with home and other absolute paths made portable."""
    dropped: list[str] = []
    return _clean(data, project_root, _home(), dropped), dropped


def sanitized_project_yaml(config: Any, project_root: Path, attributor: Any = None) -> tuple[str, list[str]]:
    """``(yaml text, dropped fields)`` of the project's configuration for a share bundle; with ``attributor`` the name
    keys (``terms_allowed_by``) are written as the attribution setting says."""
    data = config.model_dump(mode="json", exclude_none=True) if hasattr(config, "model_dump") else dict(config)
    clean, dropped = sanitize_json(data, Path(project_root))
    if attributor is not None:
        clean = attributor.obj(clean, text_keys=set())
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True), dropped


def env_names(config: Any) -> set[str]:
    """Environment variable names the project refers to (``api_key_env`` and ``{env:NAME}`` anywhere)."""
    data = config.model_dump(mode="json") if hasattr(config, "model_dump") else config
    names: set[str] = set()

    def walk(o: Any, key: str = "") -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str):
            if key == "api_key_env" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", o):
                names.add(o)
            names.update(ENV_REF.findall(o))

    walk(data)
    return names


def scan_for_secrets(paths: list[Path], root: Path, names: set[str] | None = None) -> list[Finding]:
    """Findings in the text files among ``paths`` (relative names to ``root``). ``names``: the environment variables
    whose values must not appear."""
    entries = []
    for p in paths:
        if not p.is_file() or (p.suffix.lower() not in TEXT_SUFFIXES | {".docx"} and p.suffix):
            continue
        entries.append((str(p.relative_to(root)) if p.is_relative_to(root) else p.name, p.read_bytes()))
    return scan_entries(entries, names)


def scan_entries(entries: list[tuple[str, bytes | str]], names: set[str] | None = None) -> list[Finding]:
    """:func:`scan_for_secrets` over in-memory ``(path, content)`` entries (the package before its zip is written)."""
    values = []
    for n in sorted(names or ()):
        v = os.environ.get(n)
        if v and len(v) >= MIN_SECRET_LEN:
            values.append((n, v))
    home = _home()
    out: list[Finding] = []
    for rel, content in entries:
        if Path(rel).suffix.lower() == ".docx" and isinstance(content, bytes):
            text = docx_text(content)   # D23: a Word file is a zip; its line is the paragraph number
        elif Path(rel).suffix and Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            continue
        else:
            try:
                text = content.decode("utf-8") if isinstance(content, bytes) else content
            except UnicodeDecodeError:
                continue
        for i, line in enumerate(text.splitlines(), 1):
            for n, v in values:
                if v in line:
                    out.append(Finding(rel, i, f"value of environment variable {n}"))
            for kind, rx in KEY_SHAPES:
                if rx.search(line):
                    out.append(Finding(rel, i, kind))
            if len(home) > 1 and home in line:
                out.append(Finding(rel, i, "home-folder path"))
    return out


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX_PARTS = re.compile(r"^(word/(document|header\d*|footer\d*)\.xml|docProps/[^/]+\.xml)$")
DOCX_NAME_TAGS = {"creator", "lastModifiedBy", "Manager", "Company"}


def docx_paragraphs(data: bytes):
    """``(style, text, cells, column, name_field)`` per paragraph of a .docx (D23): ``word/document.xml`` first, then
    its headers, footers and ``docProps/*.xml``. A paragraph's ``w:t`` texts are joined without a separator, so a
    string split over two runs is one string, and the XML parser unescapes entities. ``cells`` are the texts of the
    table row a paragraph sits in (``column`` its cell), else None; a property's text has ``name_field`` when the
    property names a person."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        parts = sorted((n for n in z.namelist() if DOCX_PARTS.match(n)),
                       key=lambda n: (n != "word/document.xml", n.startswith("docProps/"), n))
    except (zipfile.BadZipFile, ValueError):
        return

    def para(p) -> str:
        return "".join(t.text or "" for t in p.iter(f"{_W}t"))

    def walk(el, cells=None, col=None):
        for ch in el:
            if ch.tag == f"{_W}p":
                st = ch.find(f"{_W}pPr/{_W}pStyle")
                yield (st.get(f"{_W}val") if st is not None else ""), para(ch), cells, col, False
            elif ch.tag == f"{_W}tbl":
                for tr in ch.findall(f"{_W}tr"):
                    tcs = tr.findall(f"{_W}tc")
                    texts = ["\n".join(para(p) for p in tc.iter(f"{_W}p")) for tc in tcs]
                    for i, tc in enumerate(tcs):
                        yield from walk(tc, texts, i)
            else:
                yield from walk(ch, cells, col)

    for n in parts:
        try:
            root = ElementTree.fromstring(z.read(n))
        except ElementTree.ParseError:
            continue
        if n.startswith("docProps/"):
            for el in root.iter():
                if el.text and el.text.strip():
                    yield "", el.text, None, None, el.tag.rsplit("}", 1)[-1] in DOCX_NAME_TAGS
        else:
            yield from walk(root)


def docx_text(data: bytes) -> str:
    """The text of a .docx for the secret scan (D23): one line per paragraph, see :func:`docx_paragraphs`."""
    return "\n".join(t.replace("\n", " ") for _s, t, _c, _k, _n in docx_paragraphs(data))


def scan_tree(root: Path, names: set[str] | None = None) -> list[Finding]:
    return scan_for_secrets(sorted(p for p in Path(root).rglob("*") if p.is_file()), Path(root), names)


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------------------------- researcher names
BIB_COLUMNS = {"title", "journal", "authors", "doi", "abstract"}
BIB_KEYS = BIB_COLUMNS | {"title_norm", "terms", "query"}
NAME_COLUMNS = {"author", "by", "reviewer", "reader", "accepted_by", "first_by", "choice_by", "answered_by",
                "labels_used", "terms_allowed_by", "allowed_by", "created_by", "reused_by"}
RESEARCHER_TEXT = {"methods.md": None, "labels.csv": {"reason"}}   # text a researcher typed: file, or its columns
# the PRISMA reporting files (D25): only the researcher's fields are typed, and a finding names the item
REPORT_TITLES = {"prisma_2020_checklist": "PRISMA 2020", "prisma_s_checklist": "PRISMA-S", "prisma_s": "PRISMA-S"}
REPORT_TYPED = {"researcher text", "where reported", "not applicable reason"}
REPORT_TYPED_KEYS = {"text", "where", "not_applicable"}
STRATEGY_ITEM = ("PRISMA-S", "8")   # its WIDER lines are the search strategies: bibliographic, not scanned
_ITEM_HEAD = re.compile(r"^#{2,3} (\w+)\. ")
_METHODS_HEAD = re.compile(r"^## (PRISMA 2020|PRISMA-S) item (\w+): ")
WIDER_BUG = "was left in a file WIDER wrote; this is a WIDER bug, please report it"
MIN_NAME_LEN = 3


class NameFound(SecretFound):
    """A shared output held a researcher name the attribution setting hides; the output was deleted."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        shown = "; ".join(f"{f.file} line {f.line}: {f.kind}" for f in findings[:10]) + (
            f"; and {len(findings) - 10} more" if len(findings) > 10 else "")
        ValueError.__init__(self, f"refused: the output would contain a researcher name the attribution setting hides "
                                  f"({shown}); nothing was written")


def _bib_file(path: str) -> bool:
    p = path.replace("\\", "/")
    parts = p.split("/")
    return "search_strings" in parts[:-1] or "queries" in parts[:-1]


def _walk(obj: Any, line_of, name_field: bool = False, keyed: bool = False):
    """``(line, text, is_name_field)`` for every string of a JSON value outside the bibliographic keys."""
    from .researchers import KEYED_KEYS, NAME_KEYS
    if isinstance(obj, dict):
        for k, v in obj.items():
            if keyed:
                yield line_of(k), str(k), True
            if k in BIB_KEYS:
                continue
            yield from _walk(v, line_of, k in NAME_KEYS, k in KEYED_KEYS)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, line_of, name_field)
    elif isinstance(obj, str):
        yield line_of(obj), obj, name_field


def _report_units(name: str, text: str):
    """:func:`_units` for a PRISMA reporting file: ``typed`` is the item's label for the researcher's fields and
    False for WIDER's lines."""
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    title = REPORT_TITLES.get(stem, "")

    def lab(no: Any, t: str = title) -> str:
        return f"{t} item {no}"

    if suffix == ".csv":
        r = csv.reader(io.StringIO(text))
        header = next(r, None) or []
        for row in r:
            no = row[0] if row else ""
            for h, cell in zip(header, row):
                if not cell or (h == "WIDER evidence" and (title, no) == STRATEGY_ITEM):
                    continue
                yield r.line_num, cell, False, lab(no) if h in REPORT_TYPED else False
        return
    if suffix == ".json":
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            lines = text.splitlines()

            def line_of(s: str) -> int:
                frag = json.dumps(s, ensure_ascii=False)[1:-1][:60]
                return next((i for i, x in enumerate(lines, 1) if frag in x), 1)

            for it in obj.get("items") or []:
                if not isinstance(it, dict):
                    continue
                no = str(it.get("no", ""))
                for k, v in it.items():
                    if k == "researcher" and isinstance(v, dict):
                        for rk, rv in v.items():
                            for ln, s, nf in _walk(rv, line_of):
                                yield ln, s, nf, lab(no) if rk in REPORT_TYPED_KEYS else False
                    elif not (k == "lines" and (title, no) == STRATEGY_ITEM):
                        for ln, s, nf in _walk({k: v}, line_of):
                            yield ln, s, nf, False
            rest = {k: v for k, v in obj.items() if k != "items"}
            for ln, s, nf in _walk(rest, line_of):
                yield ln, s, nf, False
            return
    # Markdown: a checklist (item headings, then "WIDER recorded" and "Researcher text") or methods_text.md
    item, part = None, None
    for i, line in enumerate(text.splitlines(), 1):
        m, mm = _ITEM_HEAD.match(line), _METHODS_HEAD.match(line)
        if mm and stem == "methods_text":
            item, part = (mm.group(1), mm.group(2)), "typed"
            yield i, line, False, False
            continue
        if m and stem != "methods_text":
            item, part = (title, m.group(1)), "wider"
            yield i, line, False, False
            continue
        if item and line.startswith("#") and line.lstrip("#").strip() in ("WIDER recorded", "Researcher text"):
            part = "typed" if line.lstrip("#").strip() == "Researcher text" else "wider"
            yield i, line, False, False
            continue
        if item and stem == "methods_text" and line.strip("*") == "WIDER draft, not reviewed by the researcher":
            part = "wider"
        if item == STRATEGY_ITEM and part == "wider":
            continue
        typed = part == "typed" and not line.startswith(("Your text: ", "Status: "))
        yield i, line, False, lab(item[1], item[0]) if typed and item else False


def _units(path: str, text: str):
    """``(line, text, is_name_field, typed_by_a_researcher)`` per piece of a file that the name scan reads; for a
    PRISMA reporting file ``typed`` is the item's label or False."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if Path(name).stem in REPORT_TITLES or name == "methods_text.md":
        yield from _report_units(name, text)
        return
    typed = RESEARCHER_TEXT.get(name, ())
    suffix = Path(name).suffix.lower()
    if suffix in (".csv", ".tsv"):
        r = csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",")
        header = next(r, None) or []
        for row in r:
            for h, cell in zip(header, row):
                if h not in BIB_COLUMNS and cell:
                    yield r.line_num, cell, h in NAME_COLUMNS, typed is None or h in (typed or ())
        return
    if suffix == ".jsonl":
        for i, line in enumerate(text.splitlines(), 1):
            try:
                obj = json.loads(line)
            except ValueError:
                yield i, line, False, typed is None
                continue
            for _, s, nf in _walk(obj, lambda _s, i=i: i):
                yield i, s, nf, typed is None
        return
    if suffix == ".json":
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if obj is not None:
            lines = text.splitlines()

            def line_of(s: str) -> int:
                frag = json.dumps(s, ensure_ascii=False)[1:-1][:60]
                return next((i for i, x in enumerate(lines, 1) if frag in x), 1)

            for ln, s, nf in _walk(obj, line_of):
                yield ln, s, nf, typed is None
            return
    whole = name == "project.yaml"
    for i, line in enumerate(text.splitlines(), 1):
        yield i, line, whole, typed is None


DOCX_TYPED_LABEL = "Researcher text"
DOCX_DRAFT_LABEL = "WIDER draft, not reviewed by the researcher"
_DOCX_ITEM = re.compile(r"^(PRISMA 2020|PRISMA-S) item (\w+): ")


def _docx_units(path: str, data: bytes):
    """:func:`_units` for a Word file (D23, D25), the line being the paragraph number of :func:`docx_text`. In the
    checklists the "where reported" column is the researcher's; in ``methods_documentation.docx`` the blocks under
    "Researcher text" and a not-applicable reason are, the WIDER drafts are not, and the search strategies are
    skipped like ``search_strings/`` so a search term is never refused."""
    stem = Path(path.replace("\\", "/").rsplit("/", 1)[-1]).stem
    title = REPORT_TITLES.get(stem)
    section, item, part = None, None, None
    for i, (style, text, cells, col, name_field) in enumerate(docx_paragraphs(data), 1):
        typed: Any = False
        if title:
            if cells is not None and len(cells) == 4 and col == 3:
                typed = f"{title} item {cells[1]}"
        elif stem == "methods_documentation":
            if style == "Heading1":
                section, item, part = text, None, None
            elif section == "Search strategies":
                continue
            elif section == "Methods text" and cells is None:
                m = _DOCX_ITEM.match(text) if style == "Heading2" else None
                if m:
                    item, part = (m.group(1), m.group(2)), "start"
                elif text in (DOCX_TYPED_LABEL, DOCX_DRAFT_LABEL):
                    part = "typed" if text == DOCX_TYPED_LABEL else "wider"
                elif item:
                    if part == "start" and text.startswith("Not applicable: "):
                        part = "typed"
                    if part == "typed":
                        typed = f"{item[0]} item {item[1]}"
        yield i, text, name_field, typed


def _word(names: list[str]) -> re.Pattern | None:
    if not names:
        return None
    alts = sorted(set(names), key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in alts) + r")(?!\w)")


def scan_for_names(entries: list[tuple[str, bytes | str]], hidden: dict[str, str], unsigned: list[str] = (),
                   inferred: set[str] = frozenset(), login: set[str] = frozenset()) -> list[Finding]:
    """Findings for the names an output must not hold (D6), run after :func:`scan_for_secrets`.

    ``entries``: ``(path in the output, content)``. ``hidden``: name -> pseudonym, each looked for as a whole word,
    case-sensitive, from 3 characters, outside the bibliographic columns and keys. ``unsigned``: the login and
    ``$WIDER_REVIEWER`` values that never signed, looked for only in name fields, paths and ``project.yaml``.
    ``inferred`` names get the hint to confirm them (``names`` mode); ``login`` names are this computer's login."""
    # a name of the form "Researcher n" (an older project, $USER) reads as the pseudonyms the file holds: not looked for
    from .journal import PSEUDONYM_FORM
    pat = _word([n for n in hidden if len(n) >= MIN_NAME_LEN and not PSEUDONYM_FORM.fullmatch(n)])
    upat = _word([u for u in unsigned if u])
    out: list[Finding] = []
    for path, content in entries:
        if _bib_file(path):
            continue
        if Path(path).suffix.lower() == ".docx" and isinstance(content, bytes):
            units = _docx_units(path, content)
        elif Path(path).suffix and Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        else:
            try:
                text = content.decode("utf-8") if isinstance(content, bytes) else content
            except UnicodeDecodeError:
                continue
            units = _units(path, text)
        seen: set[tuple[int, str]] = set()
        for line, s, name_field, typed in units:
            hits = [(m.group(1), False) for m in pat.finditer(s)] if pat else []
            if upat:
                if name_field:
                    hits += [(m.group(1), True) for m in upat.finditer(s)]
                else:
                    for tok in re.findall(r"[^\s\"'`|]*[/\\][^\s\"'`|]*", s):
                        parts = set(re.split(r"[/\\]", tok))
                        hits += [(u, True) for u in unsigned if u in parts]
            for n, is_unsigned in hits:
                if (line, n) in seen:
                    continue
                seen.add((line, n))
                if is_unsigned or n in login:
                    what = "the login name of this computer"
                else:
                    what = f"the name of {hidden.get(n, 'a researcher')}"
                if isinstance(typed, str):
                    kind = f"{typed}: {what}; edit your text"
                else:
                    kind = f"{what}; edit the file" if typed else f"{what} {WIDER_BUG}"
                if n in inferred:
                    kind += "; confirm the researcher first to write the name"
                out.append(Finding(path, line, kind))
    return out


def scan_names_in_tree(root: Path, attributor: Any) -> list[Finding]:
    root = Path(root)
    entries = [(str(p.relative_to(root)), p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()]
    return scan_with(entries, attributor)


def scan_with(entries: list[tuple[str, bytes | str]], attributor: Any) -> list[Finding]:
    """:func:`scan_for_names` with what ``attributor`` (a :class:`~wider.core.researchers.Attributor`) hides."""
    from .researchers import login_names
    hidden = attributor.hidden()
    inferred = {n for n in hidden if attributor.mode == "names" and attributor.is_inferred(n)}
    return scan_for_names(entries, hidden, attributor.unsigned(), inferred, login_names())
