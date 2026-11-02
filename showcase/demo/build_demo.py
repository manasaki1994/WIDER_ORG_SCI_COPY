#!/usr/bin/env python3
"""Build the interactive demo into a static folder (openspec change interactive-demo, design D1, D2, D9, D10).

    python3 showcase/demo/build_demo.py                 # writes showcase/site/demo/
    python3 showcase/demo/build_demo.py --out DIR
    python3 showcase/demo/build_demo.py --skip-coverage # a local look while a checkpoint is still missing

The folder holds every file of `wider/ui/static/` byte for byte, `index.html` with the few edits listed in D1, the
demo layer (`demo.js`, `actions.js`, `terms.js`, `tour.js`, `demo.css`) and `snapshot.js`, the recorded answers as a script.
Nothing is written when an `/api/` call of the interface has no answer in the demo (unless ``check_coverage`` is
off), when a file would load something from outside the page, or when the privacy scan finds a key-like string, a
computer path, the login name or a word the repository's privacy guard refuses. The error names the file and the kind
of string.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, file: str):
    # by path and under its own name: `coverage` is also the name of a common package
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cov = _load("demo_coverage", "coverage.py")
scrub = _load("demo_scrub", "scrub.py")

STATIC = REPO / "wider" / "ui" / "static"
SNAPSHOT = HERE / "snapshot"
COVERAGE = HERE / "coverage.json"
OUT = REPO / "showcase" / "site" / "demo"
LAYER = ("demo.css", "demo.js", "actions.js", "screening.js", "ask.js", "terms.js", "tour.js")
REQUIRED_LAYER = ("demo.css", "demo.js")
CHECKPOINTS = ("imported", "ranked", "sampled", "screened", "screened2")
CSP = ("default-src 'self'; connect-src 'none'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
TITLE_PREFIX = "Demo — "
TEXT_SUFFIXES = {".html", ".js", ".css", ".txt", ".json", ".svg", ".md"}

_SCHEME = r"(?:(?!data:|blob:)[a-z][a-z0-9+.-]*:|//)"
# what the browser would load by itself from outside the page (an <a href> is not: the demo's click catcher has it)
_OUTSIDE = [
    ("a src attribute", re.compile(rf"\bsrc\s*=\s*[\"']{_SCHEME}", re.IGNORECASE)),
    ("a <link href>", re.compile(rf"<link\b[^>]*\bhref\s*=\s*[\"']{_SCHEME}", re.IGNORECASE)),
    ("a CSS url()", re.compile(rf"url\(\s*[\"']?{_SCHEME}", re.IGNORECASE)),
    ("a CSS @import", re.compile(r"@import\b", re.IGNORECASE)),
    ("a fetch of an outside address", re.compile(rf"\bfetch\(\s*[\"'`]{_SCHEME}", re.IGNORECASE)),
    ("an import of an outside address", re.compile(rf"\bimport\(\s*[\"'`]{_SCHEME}", re.IGNORECASE)),
]


class BuildError(Exception):
    """The build refused; the message names the call or the file and the kind of string."""


def outside_addresses(text: str) -> list[str]:
    """The kinds of outside load in a page, script or style file (empty when it loads nothing from outside)."""
    return [kind for kind, rx in _OUTSIDE if rx.search(text)]


def scan(files: dict[str, bytes], user: str | None = None) -> list[str]:
    """Every problem in the bundle, as ``"<file>: <kind>"``: outside loads and what ``scrub.problems`` refuses."""
    out = []
    for name in sorted(files):
        if Path(name).suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = files[name].decode("utf-8")
        out += [f"{name}: {kind}" for kind in outside_addresses(text)]
        out += [f"{name}: {kind}" for kind in scrub.problems(text, user)]
    return out


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise BuildError(f"index.html: expected {old!r} once, found it {text.count(old)} times (the app changed; "
                         "update build_demo.py)")
    return text.replace(old, new)


def edit_index(html: str, layer: list[str]) -> str:
    """The app's index.html with the demo's edits (D1): CSP first in <head>, `/static/` → `/demo/`, the title
    prefix, demo.css after style.css, snapshot.js, demo.js, actions.js, screening.js and ask.js before common.js, terms.js and
    tour.js after shell.js. Every script of LAYER is placed here: a layer file the page does not load would be copied
    and do nothing."""
    html = _replace_once(html, "<head>\n", f'<head>\n<meta http-equiv="Content-Security-Policy" content="{CSP}">\n')
    html = html.replace("/static/", "/demo/")
    html = _replace_once(html, "<title>WIDER</title>", f"<title>{TITLE_PREFIX}WIDER</title>")
    style = '<link rel="stylesheet" href="/demo/style.css">'
    if "demo.css" in layer:
        html = _replace_once(html, style, f'{style}\n<link rel="stylesheet" href="/demo/demo.css">')
    common = '<script src="/demo/common.js"></script>'
    first = [f'<script src="/demo/{f}"></script>' for f in ("snapshot.js", "demo.js", "actions.js", "screening.js", "ask.js")
             if f == "snapshot.js" or f in layer]
    html = _replace_once(html, common, "\n".join(first + [common]))
    late = [f'<script src="/demo/{f}"></script>' for f in ("terms.js", "tour.js") if f in layer]
    if late:
        shell = '<script src="/demo/shell.js"></script>'
        html = _replace_once(html, shell, "\n".join([shell] + late))
    return html


def terms_data(here: Path = HERE) -> dict:
    """What the terms panel counts on (D6): the hand-written blocks of synonyms.yaml and the invented papers (one
    unique record each) with their reference decision."""
    blocks = yaml.safe_load((here / "synonyms.yaml").read_text(encoding="utf-8"))["blocks"]
    papers = yaml.safe_load((here / "papers.yaml").read_text(encoding="utf-8"))["papers"]
    return {"blocks": [{"name": b["name"], "terms": list(b["terms"])} for b in blocks],
            "records": [{"id": p["id"], "title": p["title"], "abstract": p["abstract"], "decision": p["decision"]}
                        for p in papers]}


WHO = ("You decide", "WIDER does")
# the namespaces of the tour's numbers and the checkpoint each comes from: a step or card quoting one whose checkpoint
# is not recorded is dropped (a card falls back), an unknown namespace or key is a mistake in tour.yaml
SOURCES = {"kp": "imported", "imp": "imported", "dd": "imported", "rk": "ranked", "sm": "sampled",
           "sc1": "screened", "sc2": "screened2", "rc": "screened"}
_PLACE = re.compile(r"\{([a-z0-9]+)\.([a-z0-9_]+)\}")


def _answer(snapshot: Path, bodies: dict, checkpoint: str, key: str):
    f = snapshot / f"checkpoint-{checkpoint}.json"
    if not f.exists():
        return None
    ref = json.loads(f.read_text(encoding="utf-8"))["answers"].get(key)
    return bodies[ref].get("json") if ref else None


def _num(x) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def tour_values(snapshot: Path) -> dict[str, dict[str, str]]:
    """The numbers the tour quotes, by namespace, read from the recorded answers (D7): ``kp`` the seed papers,
    ``imp`` the import, ``dd`` deduplication, ``rk`` ranking, ``sm`` the samples, ``sc1`` and ``sc2`` the two screening
    rounds, ``rc`` the recall estimate (with ``caveat``, what its interval does and does not say, ``why`` how it is worked out
    and ``advice`` what a researcher can do about it). A namespace whose checkpoint is not
    recorded is missing; the steps that quote it are dropped."""
    bodies = json.loads((snapshot / "bodies.json").read_text(encoding="utf-8"))
    get = lambda cp, key: _answer(snapshot, bodies, cp, key)  # noqa: E731
    out: dict[str, dict[str, str]] = {}
    bench = get("imported", "GET /api/benchmark")
    if bench is not None:
        sets = [r.get("set_name") for r in bench]
        out["kp"] = {"n": str(len(bench)), "dev": str(sets.count("Development")), "val": str(sets.count("Validation"))}
    imp, check = get("imported", "GET /api/import"), get("imported", "GET /api/import/check")
    if imp and check:
        found = {b for f in check["files"] for b in f.get("benchmark_found", [])}
        out["imp"] = {"files": str(len(imp["files"])), "records": str(sum(r["records"] for r in imp["summary"])),
                      "per": ", ".join(f"{r['platform']} {r['records']}" for r in imp["summary"]),
                      "found": str(len(found))}
    dd, dd2 = get("imported", "GET /api/dedup"), get("ranked", "GET /api/dedup")
    if dd:
        fl = dd["flow"]
        out["dd"] = {"imported": str(fl["imported"]), "auto": str(fl["removed_auto"]), "left": str(fl["left"]),
                     "pairs": str(fl["to_decide"]), "final": str(dd2["flow"]["left"]) if dd2 else None,
                     "removed": str(fl["imported"] - dd2["flow"]["left"]) if dd2 else None}
    rank, val = get("ranked", "GET /api/rank?limit=50&offset=0"), get("ranked", "GET /api/rank/validate")
    if rank and val:
        n, top = rank["n_rows"], rank["rows"][0]
        ex = min((r for r in val["rows"] if r.get("found") and r["set"] == "Validation"), key=lambda r: r["rank"])
        summ = val["summary"]
        out["rk"] = {"n": str(n), "top_title": top["title"], "top_score": f"{top['score']:.3f}",
                     "top_pct": str(round(top["score"] * 100)), "ex_title": ex["title"], "ex_rank": str(ex["rank"]),
                     "ex_pct": str(math.ceil(100 * ex["rank"] / n)),
                     "dev_n": str(summ["Development"]["n"]), "dev_median": _num(summ["Development"]["median_rank"]),
                     "val_n": str(summ["Validation"]["n"]), "val_median": _num(summ["Validation"]["median_rank"])}
    smp = get("sampled", "GET /api/samples")
    if smp:
        dev, vl = smp["samples"]["development"], smp["samples"]["validation"]
        done = sum(r["done"] for r in dev["progress"]["reviewers"].values())
        out["sm"] = {"dev_n": str(dev["n"]), "val_n": str(vl["n"]), "dev_done": str(done),
                     "dev_open": str(dev["n"] - done)}
    sets = get("screened", "GET /api/screen/sets?protocol=main")
    if sets:
        s1 = next(x for x in sets["sets"] if x["result_set"] == 1)
        m1 = get("screened", "GET /api/stages")["last_results"]["screen"]["metrics"]
        b1 = s1["settings"]["backend"]
        out["sc1"] = {"n": str(s1["n"]), "model": f"{b1['model']} at temperature {_num(b1['temperature'])}",
                      "include": str(s1["include"]), "maybe": str(s1["maybe"]), "exclude": str(s1["exclude"]),
                      "disagree": str(m1["disagreements"])}
    cmp2 = get("screened2", "GET /api/screen/sets/main/compare?a=1&b=2")
    if cmp2:
        s2 = next(x for x in get("screened2", "GET /api/screen/sets?protocol=main")["sets"] if x["result_set"] == 2)
        m2 = get("screened2", "GET /api/stages")["last_results"]["screen"]["metrics"]
        b2 = s2["settings"]["backend"]
        out["sc2"] = {"model": f"{b2['model']} at temperature {_num(b2['temperature'])}", "changed": str(len(cmp2["differ"])),
                      "disagree": str(m2["disagreements"]), "agree": str(cmp2["agree"]), "n": str(cmp2["n"])}
    rc = get("screened", "GET /api/recall?protocol=main&result_set=1&sample=validation")
    if rc:
        v = rc["variants"][rc["primary_variant"]]
        # the interval the app shows (Korn-Graubard, Kish at recall 0 or 1); the bootstrap only for an older recording
        lo, hi = v.get("interval") or v["ci_bootstrap"]
        n_rel = v["n_relevant"]
        kept = n_rel - v["n_missed"]
        if v.get("interval") and kept == n_rel and lo < hi:
            caveat = (f"All {n_rel} relevant records in the sample were kept, yet the interval reaches down to "
                      f"{100 * lo:.0f}%: with only {n_rel} relevant records, a screen with a much lower true recall "
                      f"could still have kept them all.")
            why = ("How it is worked out: the validation sample was drawn in score bands at different rates, so WIDER "
                   "weights each record by its band and puts exact bounds on an effective number of relevant records. "
                   "Few relevant records give a wide interval, even when none was missed.")
            advice = ("What you can do: if the lower end is too low for your decision, label a bigger validation "
                      "sample; the more relevant records it holds, all kept, the higher the lower end goes.")
        elif lo == hi:
            # worked out by the demo, not by WIDER: how often a screen that keeps 80% would still keep every relevant
            # record of the sample, and how many relevant records (all kept) push that under 5 in 100
            chance = round(100 * 0.8 ** n_rel)
            need = math.ceil(math.log(0.05) / math.log(0.8))
            caveat = (f"The interval is a single point because all {n_rel} relevant records in the sample were kept. "
                      f"That is not certainty: with only {n_rel} relevant records, a screen whose true recall is 80% "
                      f"would still keep all {n_rel} about {chance} times in 100.")
            why = (f"Why a single point: WIDER works out the interval by drawing the validation sample again many "
                   f"times from its own records. No relevant record was missed, so every new draw also gives "
                   f"{100 * v['recall']:.0f}% and the interval has no width. It shows how much this sample varies; it "
                   f"cannot show what a bigger sample would find.")
            advice = (f"What you can do: if you want to rely on the estimate, label a bigger validation sample. Only "
                      f"when about {need} relevant records are all kept would a screen whose true recall is 80% do that "
                      f"less than 5 times in 100; this sample held {n_rel} relevant records in {rc['n_labelled']}, so at "
                      f"that share it would take about {math.ceil(need * rc['n_labelled'] / n_rel)} records. The demo "
                      f"works out these numbers; the app shows the interval only.")
        else:
            caveat = "The true recall is likely somewhere in this range; a wider range means less certainty."
            why = ("WIDER works out the interval by drawing the validation sample again many times from its own "
                   "records: the spread of those draws shows how much the estimate could move with another sample of "
                   "the same size.")
            advice = "What you can do: if the range is too wide for your decision, label a bigger validation sample."
        out["rc"] = {"est": f"{100 * v['recall']:.0f}%", "lo": f"{100 * lo:.0f}%", "hi": f"{100 * hi:.0f}%",
                     "relevant": str(n_rel), "kept": str(kept), "caveat": caveat, "why": why, "advice": advice}
    return out


def _paras(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


class _NotYet(Exception):
    """A quoted number whose checkpoint is not recorded yet."""


def _fill(text: str, values: dict[str, dict[str, str | None]], where: str) -> str:
    def one(m):
        ns, key = m.group(1), m.group(2)
        if ns not in SOURCES or (ns in values and key not in values[ns]):
            raise BuildError(f"tour.yaml: {where} quotes {{{ns}.{key}}}, which the recording does not give")
        if ns not in values or values[ns][key] is None:
            raise _NotYet(f"{ns}.{key}")
        return values[ns][key]
    return _PLACE.sub(one, text)


def tour_data(snapshot: Path = SNAPSHOT, here: Path = HERE) -> dict:
    """The guided tour (D7, D8): tour.yaml with every ``{ns.key}`` filled from the recording, the steps whose
    checkpoint (or ``needs``) is not recorded dropped, ``last`` resolved, and a concept card whose ``needs`` are not
    recorded given its ``fallback`` text."""
    src = yaml.safe_load((here / "tour.yaml").read_text(encoding="utf-8"))
    recorded = [c for c in CHECKPOINTS if (snapshot / f"checkpoint-{c}.json").exists()]
    values = tour_values(snapshot)
    steps = []
    for s in src["steps"]:
        if s.get("who") not in WHO:
            raise BuildError(f"tour.yaml: step {s.get('id')} needs who: {' or '.join(WHO)}")
        if s.get("choice") and not s.get("tradeoff"):
            raise BuildError(f"tour.yaml: step {s['id']} is a choice and needs a tradeoff")
        cp = recorded[-1] if s["checkpoint"] == "last" else s["checkpoint"]
        if cp not in CHECKPOINTS:
            raise BuildError(f"tour.yaml: step {s['id']} names an unknown checkpoint {cp}")
        if cp not in recorded or any(n not in recorded for n in s.get("needs", [])):
            continue
        step = {k: v for k, v in s.items() if k != "needs"}
        step["checkpoint"] = cp
        try:
            for k in ("title", "tradeoff"):
                if k in step:
                    step[k] = _fill(" ".join(step[k].split()), values, f"step {s['id']}")
            step["body"] = [_fill(p, values, f"step {s['id']}") for p in _paras(step["body"])]
        except _NotYet:
            continue
        steps.append(step)
    concepts = []
    for c in src["concepts"]:
        where = f"concept {c['id']}"
        try:
            if not all(n in recorded for n in c.get("needs", [])):
                raise _NotYet(c["id"])
            body = [_fill(p, values, where) for p in _paras(c["body"])]
        except _NotYet:
            if "fallback" not in c:
                raise BuildError(f"tour.yaml: {where} quotes a number not recorded yet and has no fallback") from None
            body = [_fill(p, values, where) for p in _paras(c["fallback"])]
        concepts.append({"id": c["id"], "title": c["title"], "defines": c.get("defines", []), "body": body})
    ids = {c["id"] for c in concepts}
    for s in steps:
        if s.get("concept") and s["concept"] not in ids:
            raise BuildError(f"tour.yaml: step {s['id']} links an unknown concept {s['concept']}")
    # demo-chapters-and-video D1, D2: one chapter per stage, its intro shown on its first step; a chapter whose steps
    # were all dropped is left out
    chapters, names = [], [c.get("stage") for c in src.get("chapters", [])]
    for s in steps:
        if s["stage"] not in names:
            raise BuildError(f"tour.yaml: step {s['id']} names a stage with no chapter: {s['stage']}")
    for c in src.get("chapters", []):
        if not c.get("title") or not c.get("intro"):
            raise BuildError(f"tour.yaml: chapter {c.get('stage')} needs a title and an intro")
        if any(s["stage"] == c["stage"] for s in steps):
            chapters.append({"stage": c["stage"], "title": " ".join(c["title"].split()),
                             "intro": _fill(" ".join(c["intro"].split()), values, f"chapter {c['stage']}")})
    end = dict(src["end"])
    end["intro"] = " ".join(end["intro"].split())
    return {"chapters": chapters, "steps": steps, "concepts": concepts, "end": end}


def snapshot_js(snapshot: Path, coverage: dict, user: str | None = None) -> str:
    """``window.DEMO_SNAPSHOT``: the recorded answers per checkpoint, the bodies, what the page needs from
    coverage.json (patterns and the reasons a call is not in the demo) and the terms panel's data. Scrubbed again
    (D10)."""
    bodies = json.loads((snapshot / "bodies.json").read_text(encoding="utf-8"))
    checkpoints = {}
    for name in CHECKPOINTS:
        f = snapshot / f"checkpoint-{name}.json"
        if f.exists():
            checkpoints[name] = json.loads(f.read_text(encoding="utf-8"))["answers"]
    data = {
        "checkpoints": checkpoints,
        "order": [c for c in CHECKPOINTS if c in checkpoints],
        "bodies": bodies,
        "patterns": coverage.get("patterns", {}),
        "not_in_demo": coverage.get("not_in_demo", {}),
        "handlers": coverage.get("handlers", []),
        "ask_examples": coverage.get("ask_examples", []),
        "terms": terms_data(),
        "tour": tour_data(snapshot),
    }
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "window.DEMO_SNAPSHOT = " + scrub.scrub(text, user) + ";\n"


def collect(static: Path = STATIC, snapshot: Path = SNAPSHOT, layer_dir: Path = HERE,
            coverage_path: Path = COVERAGE, user: str | None = None) -> dict[str, bytes]:
    """Every file of the bundle by its path inside the folder (nothing written)."""
    coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.exists() else {}
    missing = [f for f in REQUIRED_LAYER if not (layer_dir / f).exists()]
    if missing:
        raise BuildError(f"the demo layer is missing {', '.join(missing)} in {layer_dir}")
    layer = [f for f in LAYER if (layer_dir / f).exists()]
    files: dict[str, bytes] = {}
    for f in sorted(static.rglob("*")):
        if f.is_file() and f.name != ".DS_Store":
            rel = f.relative_to(static).as_posix()
            if rel in LAYER or rel == "snapshot.js":
                raise BuildError(f"{rel}: the app has a file with a demo layer name")
            files[rel] = f.read_bytes()
    files["index.html"] = edit_index(files["index.html"].decode("utf-8"), layer).encode("utf-8")
    for f in layer:
        files[f] = (layer_dir / f).read_bytes()
    files["snapshot.js"] = snapshot_js(snapshot, coverage, user).encode("utf-8")
    return files


def build(out: Path = OUT, *, static: Path = STATIC, snapshot: Path = SNAPSHOT, layer_dir: Path = HERE,
          coverage_path: Path = COVERAGE, check_coverage: bool = True, user: str | None = None) -> Path:
    """Write the demo folder; raises ``BuildError`` (and writes nothing) on an uncovered call or a leak."""
    if check_coverage:
        coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.exists() else {}
        layer = " ".join((layer_dir / f).read_text(encoding="utf-8") for f in LAYER if (layer_dir / f).exists())
        unhandled = [h for h in coverage.get("handlers", []) if f'D.handle("{h}"' not in layer]
        if unhandled:
            raise BuildError("coverage.json lists handlers the demo layer does not have:\n" + "\n".join(
                f"  {h}" for h in unhandled))
        left = cov.uncovered(static, coverage, cov.recorded_keys(snapshot))
        if left:
            raise BuildError("calls with no answer in the demo:\n" + "\n".join(
                f"  {k}    {', '.join(where)}" for k, where in sorted(left.items())))
    files = collect(static, snapshot, layer_dir, coverage_path, user)
    found = scan(files, user)
    # D13: the showcase site around the demo (its page, the video's caption files) holds no forbidden name either
    names = scrub.forbidden_names()
    for f in sorted(Path(out).parent.rglob("*")) if Path(out).parent.is_dir() else []:
        if f.is_file() and f.suffix.lower() in TEXT_SUFFIXES | {".vtt"} and Path(out) not in f.parents:
            if scrub.name_hits(f.read_text(encoding="utf-8", errors="replace"), names):
                found.append(f"{f.relative_to(Path(out).parent).as_posix()}: a forbidden name (D13)")
    if found:
        raise BuildError("the privacy and outside-address scan refused:\n" + "\n".join(f"  {p}" for p in found))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".demo-build-", dir=out.parent))
    try:
        for rel, data in files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_bytes(data)
        if out.exists():
            shutil.rmtree(out)
        work.rename(out)
    finally:
        if work.exists():
            shutil.rmtree(work)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--skip-coverage", action="store_true",
                    help="build even when a call has no answer (for a local look only; never for the deploy)")
    args = ap.parse_args()
    try:
        out = build(args.out, check_coverage=not args.skip_coverage)
    except BuildError as e:
        sys.exit(f"build_demo: refused. {e}")
    if args.skip_coverage:
        print("build_demo: coverage not checked; do not deploy this build", file=sys.stderr)
    n = sum(1 for f in out.rglob("*") if f.is_file())
    print(f"build_demo: wrote {n} files to {out}")


if __name__ == "__main__":
    main()
