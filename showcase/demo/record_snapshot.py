#!/usr/bin/env python3
"""Record the interactive demo's snapshot (design D4): the answers the real WIDER server gives at each checkpoint of
the invented project, scrubbed (D10), written to `showcase/demo/snapshot/`.

    python3 showcase/demo/record_snapshot.py [--only imported ranked ...] [--data ~/wider-showcase-data/demo]

A developer step, never run by the tests. Needs Google Chrome and the `websockets` package (system python3), and
the checkpoints made by `build_checkpoints.py`. For each checkpoint it:
  - copies the checkpoint folder to one fixed path (`<temp>/reviews/employee-voice`), so the project key is the same
    everywhere, and starts `wider ui` on a free local port with a fresh `WIDER_HOME`, `WIDER_REVIEWER=Reviewer A`
    no `*_API_KEY` variable (it stops when any answer reports a key) and a fixed hash seed;
  - opens every page in headless Chrome through `record.py`'s proxy, whose hook keeps each GET answer;
  - asks the GETs listed under `"record"` in `coverage.json`, each template filled from the project's own lists
    (records, stages, artifacts, runs, sets, samples, duplicate pairs, pages);
  - presses the PRISMA export (the only POST answer kept) and keeps both PRISMA files.
Each copy gets the local Ollama model (`qwen3:8b`) as its query-creation model, so Ask WIDER is offered at every
checkpoint (change demo-chapters-and-video, D12). On the last copy (`screened2`) the `ask` phase asks the example
questions of `coverage.json`'s `ask_examples` through `wider assistant ask --by "Reviewer A"` on that local model and
keeps `GET /api/assistant/history` reduced to those turns and the fields the panel shows. No cloud model, no key, and
the embedding model is loaded with the Hugging Face hub offline, so nothing leaves the computer.

Output: `snapshot/checkpoint-<name>.json` (`{"answers": {"GET /api/...?sorted query": hash}}`) and
`snapshot/bodies.json` (`{hash: {"status", "type", "json" | "text"}}`), keys sorted, bodies stored once.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import record  # noqa: E402  (showcase/record.py: the proxy and the Chrome driver)
import scrub  # noqa: E402

CHECKPOINTS = ["imported", "ranked", "sampled", "screened", "screened2"]
DATA = Path("~/wider-showcase-data/demo").expanduser()
SNAPSHOT = HERE / "snapshot"
COVERAGE = HERE / "coverage.json"
AUTHOR = "Reviewer A"
# the local model of the Ask WIDER examples (D12); it must be in `ollama list`, never pulled by this script
ASK_MODEL = "qwen3:8b"
ASK_PROVIDER = "local-qwen"
ASK_ON = "screened2"
# what the panel shows of a recorded turn; everything else (context, retrieval, usage, docs, notes, place) is dropped
ASK_FIELDS = ("n", "at", "page", "question", "answer", "based_on", "model", "answered_by")
PROJECT_KEY = "demo0employee0voice"
SAFE = "!*'()"       # the characters encodeURIComponent leaves as they are
# routes whose answers hold a model's text: a word the privacy guard flags is replaced there by a visible marker
MODEL_ROUTES = ("/api/screen/calls/", "/api/adjacent/cases/", "/api/screen/evaluate/", "/api/construct/")
STAGE = """<!doctype html><meta charset="utf-8"><script>
try { localStorage.clear(); localStorage.setItem("wider.reviewer", "Reviewer A"); } catch (e) {}
window.__stageReady = true;</script>"""


def canon(method: str, target: str) -> str:
    """The key of a request: method, path, and the query with its parameters sorted and encoded as the page does."""
    path, _, query = target.partition("?")
    pairs = sorted(urllib.parse.parse_qsl(query, keep_blank_values=True))
    q = "&".join(f"{urllib.parse.quote(k, safe=SAFE)}={urllib.parse.quote(v, safe=SAFE)}" for k, v in pairs)
    return f"{method} {path}" + (f"?{q}" if q else "")


def _hide(value):
    """A model's text with each flagged word replaced by the marker; other values as they are."""
    if isinstance(value, str):
        return scrub.hide_flagged(value)
    if isinstance(value, list):
        return [_hide(v) for v in value]
    if isinstance(value, dict):
        return {k: _hide(v) for k, v in value.items()}
    return value


class Store:
    def __init__(self, never: list[str] = ()):
        # GETs never kept (they would show what is installed on the recording computer); the demo refuses them
        self.never = [re.compile("^" + re.sub(r"\\\{\w+\\\}", "[^/?]+", re.escape(e)) + r"(\?.*)?$") for e in never]
        self.bodies: dict[str, dict] = {}
        self.answers: dict[str, str] = {}
        self.hidden: dict[str, int] = {}
        self.last = time.time()
        self.project_key = ""

    def keep(self, method: str, target: str, status: int, ctype: str, body: bytes):
        self.last = time.time()
        if method != "GET" and not target.startswith("/api/outputs/prisma"):
            return
        if not target.startswith("/api/"):
            return
        key = canon(method, target)
        if any(rx.match(key) for rx in self.never):
            return
        text = scrub.scrub(body.decode("utf-8", "replace"))
        if self.project_key:
            text = text.replace(self.project_key, PROJECT_KEY)
        is_json = "json" in ctype
        if status == 404 and is_json and json.loads(text) == {"detail": "Not Found"}:
            raise SystemExit(f"stopped: {key} has no route on this server (is it running this checkout's code?)")
        if target.startswith(MODEL_ROUTES) and is_json:
            before = text
            text = json.dumps(_hide(json.loads(text)), ensure_ascii=False)
            n = text.count(scrub.MARKER) - before.count(scrub.MARKER)
            if n:
                self.hidden[key] = n
        if re.search(r'"key_set":\s*true', text):
            raise SystemExit(f"stopped: the answer to {key} reports an API key")
        bad = scrub.problems(text)
        if bad:
            raise SystemExit(f"stopped: the answer to {key} holds {'; '.join(bad)}")
        entry = {"status": status, "type": "json" if is_json else ctype.split(";")[0]}
        if is_json:
            entry["json"] = json.loads(text)
        else:
            entry["text"] = text
        h = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]
        self.bodies[h] = entry
        self.answers[key] = h


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(base: str, target: str, author: str | None = None) -> tuple[int, str, bytes]:
    """GET a page; `author` is sent as the page sends its name (the server reads a page request's signer from it)."""
    headers = {"X-Wider-Author": urllib.parse.quote(author)} if author else {}
    try:
        r = urllib.request.urlopen(urllib.request.Request(base + target, headers=headers), timeout=120)
    except urllib.error.HTTPError as e:
        r = e
    return r.status, r.headers.get("Content-Type", ""), r.read()


def _pages() -> list[str]:
    html = (REPO / "wider" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    return re.findall(r'<section[^>]*id="page-([a-z-]+)"', html)


def _sources(base: str) -> dict[str, list[dict]]:
    """Values for the placeholders of the `"record"` templates, read from the project's own answers."""
    def j(t):
        s, _, b = _get(base, t)
        return json.loads(b) if s == 200 else {}
    html = (REPO / "wider" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    kinds = re.search(r'<select id="hist-kind">(.*?)</select>', html, re.S)
    out: dict[str, list[dict]] = {
        "pages": [{"page": p} for p in _pages()],
        "history_kinds": [{"kind": k} for k in re.findall(r'value="([a-z_]+)"', kinds.group(1) if kinds else "")],
        "coverage_scopes": [{"scope": "platforms"}, {"scope": "corpus"}],
        "decisions": [{"decision": d} for d in ("include", "maybe", "exclude")],
    }
    out["stages"] = [{"stage": s["key"]} for s in j("/api/stages").get("stages", [])]
    out["enrich_groups"] = [{"group": g} for g in j("/api/enrich/records?group=waiting&limit=50").get("labels", {})]
    out["records"] = [{"record": r["record_id"]} for r in j("/api/dedup/records?limit=100000").get("rows", [])]
    out["artifacts"] = [{"kind": k, "version": v["version"]} for k, vs in j("/api/construct").items()
                        if isinstance(vs, list) for v in vs if isinstance(v, dict) and "version" in v]
    out["kinds"] = [{"kind": k} for k in sorted({a["kind"] for a in out["artifacts"]})]
    out["samples"] = [{"sample": n} for n in j("/api/samples").get("samples", {})]
    out["pairs"] = [{"a": q["raw_a"], "b": q["raw_b"]} for q in j("/api/dedup").get("queue", [])]
    rank = j("/api/rank?offset=0&limit=1")
    out["cells"] = [{"cell": c} for c in sorted(rank.get("best_cell_top3000") or {})]
    out["rankings"] = [{"ranking": r["name"]} for r in rank.get("rankings", [])]
    out["platforms"] = [{"platform": x["key"]} for x in j("/api/platforms").get("chosen", [])]
    out["prior_reviews"] = [{"review": r["review_id"]} for r in j("/api/prior-reviews").get("reviews", [])]
    scr = j("/api/screen")
    out["runs"] = [{"run": r["run_id"]} for r in scr.get("runs", [])]
    names = sorted({p["protocol"] for p in scr.get("protocols", []) if "protocol" in p})
    out["protocols"] = [{"protocol": n} for n in names]
    out["sets"], out["set_pairs"] = [], []
    for n in names:
        nums = [s["result_set"] for s in j("/api/screen/sets?protocol=" + urllib.parse.quote(n, safe=SAFE)).get("sets", [])]
        out["sets"] += [{"protocol": n, "set": k} for k in nums]
        out["set_pairs"] += [{"protocol": n, "a": x, "b": y} for x in nums for y in nums if x < y]
    return out


def _expand(template: dict, sources: dict[str, list[dict]]) -> list[str]:
    groups = [sources.get(name, []) for name in template.get("from", [])]
    out = []
    for combo in itertools.product(*groups):
        values = {k: urllib.parse.quote(str(v), safe=SAFE) for d in combo for k, v in d.items()}
        out.append(template["path"].format(**values))
    return out


def _wider(env: dict, proj: Path, *args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([str(REPO / ".venv" / "bin" / "wider"), *args, "--dir", str(proj)], env=env, cwd=proj.parent,
                          capture_output=True, text=True, timeout=timeout)


def check_ask_model() -> None:
    """Stop unless the local model is installed; pulling it is a download the user decides on."""
    try:
        listed = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        listed = ""
    if not re.search(r"^" + re.escape(ASK_MODEL) + r"\s", listed, re.M):
        raise SystemExit(f"stopped: `ollama list` does not show {ASK_MODEL}; the Ask WIDER examples need it")


def use_local_model(env: dict, proj: Path) -> None:
    for args in (("providers", "add", "--preset", "ollama", "--name", ASK_PROVIDER, "--model", ASK_MODEL),
                 ("providers", "stage", "construct", ASK_PROVIDER)):
        r = _wider(env, proj, *args, timeout=120)
        if r.returncode:
            raise SystemExit(f"stopped: wider {' '.join(args)} failed: {r.stderr.strip()[-400:]}")


def ask_examples(env: dict, proj: Path, examples: list[dict]) -> None:
    """Ask each example as Reviewer A on the local model; stop on a turn with no answer or no source."""
    for ex in examples:
        page = ex.get("page") or "general"
        args = ["assistant", "ask", ex["question"], "--by", AUTHOR, "--allow-once", "--json"]
        if page != "general":
            args += ["--page", page]
        r = _wider(env, proj, *args)
        try:
            turn = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise SystemExit(f"stopped: no answer to {ex['question']!r}: {r.stderr.strip()[-400:]}") from None
        if not str(turn.get("answer") or "").strip() or not turn.get("based_on"):
            raise SystemExit(f"stopped: the answer to {ex['question']!r} has no text or no source")
        print(f"asked: {ex['question']}")


def example_turns(history: dict, examples: list[dict]) -> dict:
    """The recorded history reduced to the examples' last turns, in `ask_examples` order, numbered from 1."""
    if history.get("author") != AUTHOR:   # D13: the demo records no other signer
        raise SystemExit(f"stopped: the conversation is signed {history.get('author')!r}, not {AUTHOR!r}")
    turns = history.get("turns") or []
    out = []
    for ex in examples:
        mine = [t for t in turns if t.get("question") == ex["question"]]
        if not mine:
            raise SystemExit(f"stopped: no recorded turn for {ex['question']!r}")
        t = {k: mine[-1][k] for k in ASK_FIELDS if k in mine[-1]}
        t["n"] = len(out) + 1
        out.append(t)
    return {"author": AUTHOR, "turns": out}


async def record_checkpoint(name: str, data: Path, store: Store, coverage: dict) -> None:
    work = Path(tempfile.gettempdir()) / "wider-demo-record"     # one fixed path, so every answer is the same
    shutil.rmtree(work, ignore_errors=True)
    proj = work / "reviews" / "employee-voice"
    shutil.copytree(data / name, proj)
    home = work / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(WIDER_HOME=str(home), WIDER_REVIEWER=AUTHOR, PYTHONHASHSEED="0", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1",
               PYTHONPATH=str(REPO))      # this checkout's code, not wherever the editable install points
    use_local_model(env, proj)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    srv = subprocess.Popen([str(REPO / ".venv" / "bin" / "wider"), "ui", "--dir", str(proj), "--port", str(port),
                            "--no-open"], env=env, cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(300):
            try:
                urllib.request.urlopen(base + "/api/project", timeout=2)
                break
            except Exception:
                time.sleep(0.2)
        # the project key is a hash of the folder's path on this computer: the demo uses a fixed one
        store.project_key = json.loads(_get(base, "/api/stages")[2])["project_key"]
        cfg = json.loads(_get(base, "/api/project")[2])["config"]
        backends = cfg["providers"] + cfg["embeddings"]
        if not cfg["providers"] or any(b["key_set"] for b in backends):
            raise SystemExit("stopped: the server reports an API key (or lists no provider to check); "
                             "remove every *_API_KEY variable first")
        stage = work / "stage.html"
        stage.write_text(STAGE)
        rec = record.Recorder(work / "chrome", base + "/", stage=stage)
        record._Proxy.hook = store.keep
        await rec.start()
        root = f"http://127.0.0.1:{rec.server.server_address[1]}/"
        await rec.send("Page.navigate", {"url": root})
        await rec.wait_for("document.readyState === 'complete'", 30)
        for page in _pages():
            await rec.js(f"location.hash = {json.dumps(page)}")
            await _quiet(store)
        await rec.stop()
        record._Proxy.hook = None
        for t in coverage.get("record", []):
            for target in _expand(t, _sources(base)):
                store.keep("GET", target, *_get(base, target))
        req = urllib.request.Request(base + "/api/outputs/prisma", data=b"", method="POST")
        r = urllib.request.urlopen(req, timeout=120)
        store.keep("POST", "/api/outputs/prisma", r.status, r.headers.get("Content-Type", ""), r.read())
        for f in ("exports/prisma_2020.svg", "exports/prisma_2020.json"):
            target = "/api/outputs/file?path=" + urllib.parse.quote(f, safe=SAFE)
            store.keep("GET", target, *_get(base, target))
        examples = coverage.get("ask_examples") or []
        if name == ASK_ON and examples:
            ask_examples(env, proj, examples)
            status, ctype, body = _get(base, "/api/assistant/history", AUTHOR)
            kept = example_turns(json.loads(body), examples)
            store.keep("GET", "/api/assistant/history", status, ctype, json.dumps(kept, ensure_ascii=False).encode())
    finally:
        srv.terminate()
        srv.wait(10)
        shutil.rmtree(work, ignore_errors=True)


async def _quiet(store: Store, gap: float = 1.2, limit: float = 30):
    t0 = time.time()
    await asyncio.sleep(0.3)
    while time.time() - store.last < gap and time.time() - t0 < limit:
        await asyncio.sleep(0.2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", nargs="*", default=None, help="checkpoints to record (default: every one present)")
    ap.add_argument("--data", type=Path, default=DATA)
    a = ap.parse_args()
    coverage = json.loads(COVERAGE.read_text())
    SNAPSHOT.mkdir(exist_ok=True)
    bodies_file = SNAPSHOT / "bodies.json"
    bodies = json.loads(bodies_file.read_text()) if bodies_file.exists() else {}
    names = a.only or [c for c in CHECKPOINTS if (a.data / c).is_dir()]
    check_ask_model()
    for name in names:
        store = Store(coverage.get("never_record", []))
        asyncio.run(record_checkpoint(name, a.data, store, coverage))
        bodies.update(store.bodies)
        (SNAPSHOT / f"checkpoint-{name}.json").write_text(
            json.dumps({"answers": store.answers}, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        hidden = sum(store.hidden.values())
        print(f"{name}: {len(store.answers)} answers" + (f", {hidden} words hidden in {len(store.hidden)} answers"
                                                          if hidden else ""))
        # after every checkpoint, so a later checkpoint that stops never leaves this one's answers without bodies
        bodies = write_bodies(bodies_file, bodies)
    print(f"{len(bodies)} bodies, {bodies_file.stat().st_size // 1024} KB")


def write_bodies(bodies_file: Path, bodies: dict) -> dict:
    """Keep the bodies some checkpoint file still names and write them; returns what was kept."""
    used = set()
    for f in bodies_file.parent.glob("checkpoint-*.json"):
        used |= set(json.loads(f.read_text())["answers"].values())
    bodies = {h: b for h, b in bodies.items() if h in used}
    bodies_file.write_text(json.dumps(bodies, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
    return bodies


if __name__ == "__main__":
    main()
