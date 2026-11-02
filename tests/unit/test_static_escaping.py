"""One escape helper for every HTML sink (design D9): common.js defines ``esc`` and loads first, no other file has
its own escape body, the listed template sites interpolate only escaped or known-safe values, the listed selects
are filled by ``fillSelect``, and the API returns stored names raw so escaping is the page's job."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from wider.core.project import Project
from wider.ui.app import create_app

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"

# the call that makes an interpolation safe: the shared helper, its file-local aliases, URL encoding, or the drawer's
# highlight(), which escapes every slice of the text with h() before adding <mark>
SAFE_CALL = re.compile(r"^(esc|h|adjEsc|encodeURIComponent|highlight)\(")
# known-safe values: numbers, counts, fixed-point numbers and enum or CSS-class choices between literals
ALLOW = [re.compile(p) for p in (r"^\d+$", r"\.length$", r"^[\w.]+\.toFixed\(\d\)$",
                                 # builders of a listed module that escape their own values (checked as a site below)
                                 r"^SampleExtend\.(cardHtml|bandsHtml)\(name, s\)$")]
_COND = r"[\w.\[\]]+(?: [!=]== \"\")?"
CLASS_CHOICE = re.compile(rf"\s*{_COND}\s*\?\s*\"\"\s*:\s*(?:{_COND}\s*\?\s*\"\"\s*:\s*)*\"\"\s*")

STORED = (r"\b\w+\.(title|abstract|authors|journal|doi|text|tag|author|note|reason|summary|prompt|label|name|platform"
          r"|platform_[ab]|stage|choice|answer|comment|kind|reviewer|message)\b")

# The sites listed in design.md ("Escaping" rows): (file, region start, region stop, the stored or file values
# there). Every interpolation on an HTML line of the region that mentions one of the values must be safe.
SITES = [
    ("app.js", "async function loadConstruct(", "async function loadArtifact(",
     [r"r\.(title|abstract_source|benchmark_id|set_name)", r"p\.(name|model)", r"last\.\w+", r"v\.(status|origin)"]),
    ("app.js", "function renderSummary(", 'window.addEventListener("hashchange"',
     [r"devPlatform", r"p\.key", r"c\.(title|name|default_provider|default_embedding)", r"proto\.hash"]),
    ("app.js", "async function loadQuery(", "// ---------------- import",
     [r"i\.(level|block|message|term)", r"plats\[k\]", r"dev\.\w+", r"\bk\b", r"\bs\b"]),
    ("app.js", "// ---------------- import", "// ---------------- ranking",
     [r"f\.(platform|file)", r"s\.(platform|files|records)", r"r\.(benchmark_id|method|title)", r"\bsc\b", r"\bscope\b",
      r"\bk\b", r"\bv\b"]),
    ("app.js", "// ---------------- ranking", None,
     [r"e\.name", r"r\.(name|best_cell|title|journal|platforms|run_id|benchmark_id)", r"\bc\b(?!\.)", r"x\.(terms|medoid)",
      r"p\.(name|model|protocol)", r"s\.(name|kind|ranking)", r"b\.note", r"\bname\b"]),
    ("import.js", "function renderSanity(", "async function loadImportCheck(", [r"r\.(format|verdict|n|profile)", r"\bbench\b", r"\bvocab\b"]),
    ("import.js", "async function showPriorCoverage(", None, [r"m\.(position|year|title)"]),
    ("setup.js", "async function loadSeed(", "async function loadDatabases(", [r"r\.(year|title|doi)"]),
    ("setup.js", "async function loadDatabases(", "function valueInput(", [r"export_formats", r"export_cap", r"c\.(key|label)"]),
    ("setup.js", "async function loadPrior(", None, [r"x\.(position|year|title)"]),
    ("adjacent.js", "async function loadAdjacent(", "const adjCall =", [r"e\.evidence\.cluster", r"e\.(name|label)"]),
    # backend names, models, labels, run ids and refusal texts in the screening dialogs and result-set cards
    ("backends.js", "", None, [r"\.(run_id|provider|model|label|role|role_note|source|source_protocol|scope|headline|title|record_id|protocol|cost|key)\b",
                         r"\b(detail|message|protocol)\b"]),
    # backend setup (preset cards, models list, replace dialog, consent card, header fields): names, versions, paths,
    # hosts, key variable names, destinations and warnings read from the computer or the project
    ("backends.js", "// ---------------------------------------------------------------- backend setup", None,
     [r"\.(preset|version|where|server|key_env|destination|billing|warning|checked|name|operations|label)\b", r"\b(name|value|ref|o)\b"]),
    # the backend consent dialog: backend name, destination, what is sent, what changed
    ("common.js", "function backendConsentHtml(", "function askBackendConsent(",
     [r"info\.\w+", r"\bmessage\b"]),
    # record work: titles, abstracts, notes, tags, names and answers typed by reviewers or read from export files
    *[(f, "", None, [STORED]) for f in ("records.js", "drawer.js", "history.js", "questions.js")],
    # the "Add boundary cases" card: sample names, the boundary line and the server's messages
    ("sample_extend.js", "", None, [r"\bname\b", r"\bline\b", r"\blines\b", r"\bmessage\b"]),
]


def _interpolations(line: str) -> list[str]:
    """Innermost ``${…}`` expressions of a line; one that holds a nested template is checked through its parts."""
    out, i = [], 0
    while (i := line.find("${", i)) >= 0:
        depth, j = 1, i + 2
        while j < len(line) and depth:
            depth += {"{": 1, "}": -1}.get(line[j], 0)
            j += 1
        expr = line[i + 2:j - 1]
        if "`" in expr:
            i += 2
            continue
        out.append(expr.strip())
        i = j
    return out


def _one_safe_call(e: str) -> bool:
    """``esc(…)`` and friends covering the whole expression, so ``esc(a) + b`` does not pass."""
    m = SAFE_CALL.match(e)
    if not m:
        return False
    depth = 0
    for i, ch in enumerate(e[m.end() - 1:], m.end() - 1):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0:
            return i == len(e) - 1
    return False


def _safe_part(e: str) -> bool:
    e = e.strip()
    return e == '""' or _one_safe_call(e) or any(a.search(e) for a in ALLOW)


def _collapse_safe_calls(s: str) -> str:
    """Replace each whole ``esc(…)``-style call by a literal, so operators inside its argument do not split branches."""
    out, i = "", 0
    while m := re.search(r"\b(esc|h|adjEsc|encodeURIComponent|highlight)\(", s[i:]):
        a = i + m.start()
        depth, j = 0, i + m.end() - 1
        while j < len(s):
            depth += {"(": 1, ")": -1}.get(s[j], 0)
            j += 1
            if depth == 0:
                break
        out += s[i:a] + '""'
        i = j
    return out + s[i:]


def _safe(expr: str) -> bool:
    if _safe_part(expr):
        return True
    s = re.sub(r'"[^"]*"|\'[^\']*\'', '""', expr)  # string literals are safe branches
    s = _collapse_safe_calls(s)
    if CLASS_CHOICE.fullmatch(s.replace("(", " ").replace(")", " ")):
        return True
    if "?" in s or "||" in s:
        branches = re.split(r"\?|:|\|\|", s)
        branches = branches[1:] if "?" in s else branches
        # a branch may join literals and escaped values with +
        return all(all(_safe_part(x) for x in b.split(" + ")) for b in branches)
    return False


def _region(name: str, start: str, stop: str | None) -> list[tuple[int, str]]:
    lines = (STATIC / name).read_text().splitlines()
    first = next(n for n, ln in enumerate(lines) if start in ln) if start else 0
    last = next((n for n, ln in enumerate(lines) if n > first and stop in ln), len(lines)) if stop else len(lines)
    return [(n + 1, ln) for n, ln in enumerate(lines[first:last], first)]


def test_common_js_defines_esc_and_loads_first(tmp_path):
    common = (STATIC / "common.js").read_text()
    m = re.search(r"const esc = .*", common)
    assert m and all(c in m.group(0) for c in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"))
    assert re.search(r"function fillSelect\(", common) and re.search(r"function api\(|const api = ", common)
    html = TestClient(create_app(Project.init(tmp_path / "p"))).get("/").text
    scripts = re.findall(r'<script src="/static/([\w.]+)"', html)
    assert scripts[0] == "common.js" and "app.js" in scripts


def test_no_other_escape_body():
    for f in sorted(STATIC.glob("*.js")):
        if f.name == "common.js":
            continue
        js = f.read_text()
        assert not re.search(r"replace\(/\[[^\]]*[&<>]", js), f"{f.name} builds its own escape"
        assert not re.search(r"&(lt|gt|amp|quot|#39);", js), f"{f.name} spells out entities"
        assert not re.search(r"(function (esc|h|adjEsc)\b|const (esc|h|adjEsc) = \()", js), f"{f.name} defines an escape"


def test_listed_template_sites_escape_their_values():
    bad = []
    for name, start, stop, values in SITES:
        mentions = re.compile("|".join(values))
        for n, line in _region(name, start, stop):
            if "${" not in line or not re.search(r"<\w|</", line):
                continue  # only lines that build HTML
            bad += [f"{name}:{n}: ${{{e}}}" for e in _interpolations(line) if mentions.search(e) and not _safe(e)]
    assert not bad, "unescaped interpolations:\n" + "\n".join(bad)


def test_listed_selects_use_fill_select():
    app = (STATIC / "app.js").read_text()
    for sel in ("#rk-anchors", "#ag-sample", "#ev-run", "#in-run", "#ev-sample", "#rc-protocol",
                "#rc-sample"):
        assert re.search(rf'fillSelect\("{sel}"', app), sel
    # the construct provider select goes through providerSelect, which wraps fillSelect; Screening uses the cards
    assert re.search(r"function providerSelect\([^)]*\) \{\n[^}]*fillSelect\(", app)
    assert 'providerSelect("#art-provider"' in app and 'Pickers.llmPicker($("#sc-models")' in app
    # an optional parameter left at its placeholder is left out of the request
    assert "reportParams(choice)" in app and 'anc ? "&anchors_version="' in app
    assert 'sample ? "?sample="' in (STATIC / "records.js").read_text()
    assert 'sample ? "?sample="' in (STATIC / "adjacent.js").read_text()


def test_api_returns_stored_names_raw(tmp_path):
    # the API keeps a sample name as typed; the page escapes it where it enters the HTML (option, table, heading)
    from wider.ingest.importer import add_file, import_all
    from wider.sample.labels import import_sample_csv

    p = Project.init(tmp_path / "p", platforms=["scopus"])
    (tmp_path / "a.ris").write_text("".join(f"TY  - JOUR\nTI  - Sample paper number {i} on topic alpha\nPY  - 2020\n"
                                            f"DO  - 10.1000/s{i}\nER  -\n\n" for i in range(3)))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    (tmp_path / "s.csv").write_text("title,doi,decision\nSample paper number 0 on topic alpha,10.1000/s0,Included\n")
    name = "<b>x</b>"
    import_sample_csv(p, name, tmp_path / "s.csv", kind="purposive")
    c = TestClient(create_app(p))
    assert name in c.get("/api/samples").json()["samples"]
    assert any(s["name"] == name for s in c.get("/api/screen").json()["samples"])
