#!/usr/bin/env python3
"""Record the WIDER showcase video: a scripted walk through a running WIDER UI, captured frame by frame with headless
Chrome over the DevTools protocol, then encoded to MP4 by showcase/encode.swift.

Usage: python3 showcase/record.py OUTDIR [--site DIR | --port 8780] [--only SCENE ...] [--no-encode]
                                  [--scenes FILE.py] [--stage FILE.html] [--project NAME] [--bitrate BPS]
Needs: Google Chrome, the `websockets` package (any Python that has it) and Swift (Xcode command-line tools).

The app runs in an iframe on a 1600x900 "stage" (rendered at 1.2x = 1920x1080) under a caption band; a drawn cursor
moves to and clicks real elements. So that the stage may reach into the iframe without turning off the browser's
security, a small proxy on 127.0.0.1 serves the stage and passes every other request to the WIDER server: stage and
app share one origin. Chrome only ever opens that proxy. Frames are only captured when something changes; holds repeat a
frame in the encoder, so a 3-minute video needs a few hundred screenshots. Folder paths in the page are replaced
with ~/reviews/<name> so no local path shows in the video; <name> is the project folder's name (--project).

With --site DIR (the showcase video, filmed from the built offline demo) the same server serves that static folder
instead of proxying a WIDER server: GET only, from 127.0.0.1 only, and a path outside the folder is refused with 404.
The iframe then opens /demo/. A scene may mark cues (rec.cue): the chapters and the captions are written as two WebVTT
files next to the video, with times taken from the joined takes.

--scenes and --stage take another script and stage (both default to the files next to this one), so a video of
another project can keep its own script outside the repository.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import http.server
import importlib.util
import json
import math
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FPS = 30
W, H, DSF = 1600, 900, 1.2
DEBUG_PORT = 9333


def ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


class _Proxy(http.server.BaseHTTPRequestHandler):
    """Serves the stage at /__stage__.html and forwards everything else to the app. A scene script may answer some GET
    paths itself (its ROUTES: path -> function of the query string returning JSON-able data), for demo-only views.
    A hook, when set, is called with (method, path with query, status, content type, body) for every forwarded answer
    (the interactive demo's snapshot recorder keeps them). With a site folder set, every other GET is a file of that
    folder instead, and nothing is forwarded."""
    app = ""
    site: Path | None = None
    stage = b""
    routes: dict = {}
    hook = None
    SKIP = {"host", "connection", "accept-encoding", "origin", "referer", "content-length", "transfer-encoding",
            "content-encoding", "keep-alive"}

    def _any(self):
        path, _, query = self.path.partition("?")
        if path == "/__stage__.html":
            status, headers, body = 200, [("Content-Type", "text/html; charset=utf-8")], self.stage
        elif self.site is not None:
            status, headers, body = self._file(path)
        elif self.command == "GET" and path in self.routes:
            status, headers = 200, [("Content-Type", "application/json")]
            body = json.dumps(self.routes[path](query)).encode()
        else:
            n = int(self.headers.get("Content-Length") or 0)
            req = urllib.request.Request(self.app.rstrip("/") + self.path, data=self.rfile.read(n) if n else None,
                                         method=self.command,
                                         headers={k: v for k, v in self.headers.items() if k.lower() not in self.SKIP})
            try:
                r = urllib.request.urlopen(req, timeout=900)
            except urllib.error.HTTPError as e:
                r = e
            status, body = r.status, r.read()
            headers = [(k, v) for k, v in r.headers.items() if k.lower() not in self.SKIP]
            if _Proxy.hook:
                _Proxy.hook(self.command, self.path, status, r.headers.get("Content-Type", ""), body)
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _any

    def _file(self, path: str):
        """A file of the site folder; anything else is refused (another computer, a write, a path outside it)."""
        text = [("Content-Type", "text/plain; charset=utf-8")]
        if self.client_address[0] != "127.0.0.1":
            return 403, text, b"Only this computer may open the recording server."
        if self.command != "GET":
            return 405, text, b"The site is only read."
        rel = urllib.parse.unquote(path).lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        root = self.site.resolve()
        target = (root / rel).resolve()
        if root != target and root not in target.parents or not target.is_file():
            return 404, text, b"Not found."
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
            kind += "; charset=utf-8"
        return 200, [("Content-Type", kind)], target.read_bytes()

    def log_message(self, *args):
        pass


class Recorder:
    def __init__(self, out: Path, app_url: str, stage: Path = HERE / "stage.html", project: str = "employee-voice",
                 site: Path | None = None):
        self.out, self.app_url, self.stage_src, self.project, self.site = out, app_url, stage, project, site
        self.cues: list[tuple[str, int, str]] = []   # (kind, frames into the take, text) of the scene being filmed
        self.frames = out / "frames"
        self.scene = "scene"       # frames go to frames/<scene>/, so one scene can be filmed again on its own
        self.manifest: list[tuple[str, str, int]] = []
        self.n = 0
        self.msg_id = 0
        self.cursor = (W * 0.6, H * 0.62)

    # ---------------------------------------------------------------- chrome and the protocol
    async def start(self):
        self.frames.mkdir(parents=True, exist_ok=True)
        import websockets   # only filming needs it; the tests import this file without it
        _Proxy.app, _Proxy.site = self.app_url, self.site
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        root = f"http://127.0.0.1:{self.server.server_address[1]}/"
        app, fonts = (root + "demo/", root + "fonts/") if self.site else (root, root + "static/fonts/")
        _Proxy.stage = (self.stage_src.read_text().replace("{{FONTS_URL}}", fonts).replace("{{APP_URL}}", app)
                        .replace("{{PROJECT}}", json.dumps(self.project))
                        .replace("{{USER}}", json.dumps(getpass.getuser())).encode())
        self.proc = subprocess.Popen([
            CHROME, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={self.out / 'profile'}",
            "--hide-scrollbars", "--no-first-run",
            "--disable-features=Translate,MediaRouter", "--force-color-profile=srgb", f"--window-size={W},{H}",
            "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page = None
        for _ in range(600):     # a new profile can take many seconds to start
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/list"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.1)
        if page is None:
            self.proc.terminate()
            sys.exit(f"Chrome did not open its debugging port {DEBUG_PORT} within a minute")
        self.ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
        await self.send("Emulation.setDeviceMetricsOverride",
                        {"width": W, "height": H, "deviceScaleFactor": DSF, "mobile": False})
        await self.send("Page.enable")
        await self.send("Page.navigate", {"url": root + "__stage__.html"})
        await self.wait_for("window.__stageReady === true", 20)

    async def stop(self):
        await self.ws.close()
        self.proc.terminate()
        self.server.shutdown()

    async def send(self, method: str, params: dict | None = None):
        self.msg_id += 1
        mid = self.msg_id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr: str):
        r = await self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS error in {expr[:120]}: {r['exceptionDetails'].get('exception', {}).get('description')}")
        return r.get("result", {}).get("value")

    async def wait_for(self, cond: str, timeout: float = 10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if await self.js(f"!!({cond})"):
                    return
            except RuntimeError:
                pass
            await asyncio.sleep(0.1)
        raise TimeoutError(cond)

    # ---------------------------------------------------------------- frames
    async def shot(self) -> str:
        # clean before every frame, not only after a page opens: a page that fills in late can show a local path
        await self.js("stage.clean(), document.fonts.ready.then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))))")
        r = await self.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        self.n += 1
        name = f"frames/{self.scene}/{self.n:05d}.png"
        (self.out / name).write_bytes(base64.b64decode(r["data"]))
        return name

    async def hold(self, seconds: float):
        self.manifest.append(("frame", await self.shot(), max(1, round(seconds * FPS))))

    async def fade_in(self, frames: int = 12):
        """Cross-fade from the last captured frame to the page as it is now."""
        self.manifest.append(("fade", await self.shot(), frames))

    async def animate(self, seconds: float, step):
        """Call step(t) for t in (0, 1] once per frame and capture each frame."""
        n = max(1, round(seconds * FPS))
        for i in range(1, n + 1):
            await step(ease(i / n))
            self.manifest.append(("frame", await self.shot(), 1))

    # ---------------------------------------------------------------- the stage
    def cue(self, kind: str, text: str):
        """Mark a chapter or a caption starting now, for the WebVTT tracks."""
        self.cues.append((kind, sum(n for _, _, n in self.manifest), text))

    async def caption(self, step: str, title: str, body: str):
        await self.js(f"stage.caption({json.dumps(step)}, {json.dumps(title)}, {json.dumps(body)})")

    async def go(self, page: str, settle: float = 1.5):
        await self.js(f"stage.go({json.dumps(page)})")
        await asyncio.sleep(settle)
        await self.js("stage.clean()")

    async def card(self, kind: str, show: bool):
        await self.js(f"stage.card({json.dumps(kind)}, {json.dumps(show)})")

    async def rect(self, selector: str, text: str | None = None) -> dict:
        r = await self.js(f"stage.rect({json.dumps(selector)}, {json.dumps(text)})")
        if not r:
            raise LookupError(f"not found: {selector} {text or ''}")
        return r

    async def _target_y(self, selector: str | None, text: str | None, top: int) -> float:
        if selector is None:
            return 0
        r = await self.rect(selector, text)
        return await self.js(f"Math.max(0, Math.min(stage.win().scrollY + {r['appY']} - {top}, "
                             "stage.doc().scrollingElement.scrollHeight - stage.win().innerHeight))")

    async def jump_to(self, selector: str | None, text: str | None = None, top: int = 90):
        """Scroll at once, without frames: before a fade_in, so the scene opens on the part it is about."""
        await self.js(f"stage.win().scrollTo(0, {await self._target_y(selector, text, top)})")

    async def scroll_to(self, selector: str | None, seconds: float = 1.2, text: str | None = None, top: int = 90):
        y0 = await self.js("stage.win().scrollY")
        y1 = await self._target_y(selector, text, top)
        if abs(y1 - y0) < 2:
            return

        async def step(t):
            await self.js(f"stage.win().scrollTo(0, {y0 + (y1 - y0) * t})")
        await self.animate(seconds, step)

    async def move(self, selector: str, seconds: float = 0.8, text: str | None = None, dx: float = 0, dy: float = 0):
        r = await self.rect(selector, text)
        x0, y0 = self.cursor
        x1, y1 = r["x"] + r["w"] / 2 + dx, r["y"] + r["h"] / 2 + dy
        await self.js("stage.cursor(true)")

        async def step(t):
            # a slight arc reads as a hand moving the mouse rather than a straight robot line
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t - math.sin(math.pi * t) * min(60, abs(x1 - x0) * 0.08)
            await self.js(f"stage.moveCursor({x}, {y})")
        await self.animate(seconds, step)
        self.cursor = (x1, y1)

    async def click(self, selector: str, text: str | None = None, seconds: float = 0.8, settle: float = 1.0):
        await self.move(selector, seconds, text)
        await self.js("stage.press(true)")
        self.manifest.append(("frame", await self.shot(), 4))
        await self.js(f"stage.click({json.dumps(selector)}, {json.dumps(text)})")
        await self.js("stage.press(false)")
        await asyncio.sleep(settle)
        await self.js("stage.clean()")

    async def type(self, selector: str, value: str, per_char: float = 0.06):
        await self.js(f"stage.focus({json.dumps(selector)})")
        for i in range(1, len(value) + 1):
            await self.js(f"stage.setValue({json.dumps(selector)}, {json.dumps(value[:i])})")
            self.manifest.append(("frame", await self.shot(), max(1, round(per_char * FPS))))

    async def highlight(self, selector: str | None, text: str | None = None):
        await self.js(f"stage.highlight({json.dumps(selector)}, {json.dumps(text)})")

    async def hide_cursor(self):
        await self.js("stage.cursor(false)")

    def begin_take(self, name: str):
        """Start filming one scene: its earlier take (frames and manifest) is replaced."""
        self.scene = name
        shutil.rmtree(self.frames / name, ignore_errors=True)
        (self.frames / name).mkdir(parents=True)
        self.manifest = []
        self.cues = []

    def end_take(self):
        (self.out / "takes").mkdir(exist_ok=True)
        (self.out / "takes" / f"{self.scene}.txt").write_text(
            "".join(f"{k}\t{p}\t{n}\n" for k, p, n in self.manifest))
        (self.out / "takes" / f"{self.scene}.cues.json").write_text(json.dumps(self.cues))

    def write_tracks(self, names: list[str], folder: Path) -> list[Path]:
        """The chapters and captions WebVTT files of the joined takes, next to the video."""
        takes = []
        for name in names:
            take, cues = self.out / "takes" / f"{name}.txt", self.out / "takes" / f"{name}.cues.json"
            frames = sum(int(line.split("\t")[2]) for line in take.read_text().splitlines()) if take.exists() else 0
            takes.append((frames, json.loads(cues.read_text()) if cues.exists() else []))
        tracks = vtt_tracks(takes)
        written = []
        for kind, text in tracks.items():
            path = folder / f"wider-showcase.{kind}.vtt"
            path.write_text(text, encoding="utf-8")
            written.append(path)
        return written

    def write_manifest(self, names: list[str]) -> tuple[Path, float, list[str]]:
        """Join the takes of these scenes, in this order, into the manifest the encoder reads."""
        lines, missing = [], []
        for name in names:
            take = self.out / "takes" / f"{name}.txt"
            if take.exists():
                lines += take.read_text().splitlines(keepends=True)
            else:
                missing.append(name)
        path = self.out / "manifest.txt"
        path.write_text("".join(lines))
        return path, sum(int(line.split("\t")[2]) for line in lines) / FPS, missing


def vtt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


# a caption on screen: at most two lines of about 42 characters (the usual subtitle limits; tester 1 Nov 2026 found
# whole tour cards in one cue)
CAPTION_CHARS = 42
CAPTION_LINES = 2
_SENTENCE_END = re.compile(r"[.!?][”’\")]*$")


def caption_pieces(text: str) -> list[str]:
    """``text`` as caption cues: lines of at most CAPTION_CHARS characters (a longer word gets a line of its own),
    at most CAPTION_LINES lines per cue, and a new cue after a line that ends a sentence; a line half full ends with
    its sentence, so a cue rarely starts in the middle of one."""
    lines: list[str] = []
    for word in text.split():
        open_ = lines and not (_SENTENCE_END.search(lines[-1]) and len(lines[-1]) >= CAPTION_CHARS // 2)
        if open_ and len(lines[-1]) + 1 + len(word) <= CAPTION_CHARS:
            lines[-1] += " " + word
        else:
            lines.append(word)
    cues: list[list[str]] = [[]]
    for line in lines:
        if len(cues[-1]) >= CAPTION_LINES:
            cues.append([])
        cues[-1].append(line)
        if _SENTENCE_END.search(line):
            cues.append([])
    return ["\n".join(c) for c in cues if c]


def caption_cues(n: int, start: float, stop: float, text: str) -> list[str]:
    """The WebVTT lines of caption ``n`` shown from ``start`` to ``stop`` seconds, split by caption_pieces; the
    time is shared by length, and the pieces are numbered "n-1", "n-2", ... so a text version can join them again."""
    pieces = caption_pieces(text.replace("-->", "→"))
    if len(pieces) < 2:
        return [str(n), f"{vtt_time(start)} --> {vtt_time(stop)}", (pieces or [""])[0], ""]
    weights = [len(x) + 1 for x in pieces]
    out, t, done = [], start, 0
    for k, (piece, w) in enumerate(zip(pieces, weights), 1):
        done += w
        end = stop if k == len(pieces) else start + (stop - start) * done / sum(weights)
        out += [f"{n}-{k}", f"{vtt_time(t)} --> {vtt_time(end)}", piece, ""]
        t = end
    return out


def caption_speeds(vtt: str) -> list[tuple[str, float]]:
    """Each caption's reading speed in characters per second, the pieces of one caption counted together."""
    sums: dict[str, list[float]] = {}
    for block in vtt.strip().split("\n\n")[1:]:
        cue, timing, *text = block.splitlines()
        a, b = (sum(float(x) * f for x, f in zip(t.strip().split(":"), (3600, 60, 1))) for t in timing.split("-->"))
        got = sums.setdefault(cue.split("-")[0], [0.0, 0.0])
        got[0] += len(" ".join(text))
        got[1] += b - a
    return [(n, round(c / t, 1)) for n, (c, t) in sums.items() if t > 0]


def vtt_tracks(takes: list[tuple[int, list]]) -> dict[str, str]:
    """From the takes in video order, each (its frames, its cues as (kind, frame, text)), the WebVTT text of the
    chapters and the captions tracks. A cue lasts until the next cue of its kind or the end of the video; a caption
    is split into cues of at most two short lines (caption_cues)."""
    marks: dict[str, list[tuple[float, str]]] = {"chapters": [], "captions": []}
    start = 0
    for frames, cues in takes:
        for kind, at, text in cues:
            key = {"chapter": "chapters", "caption": "captions"}.get(kind)
            if key:
                marks[key].append(((start + at) / FPS, text))
        start += frames
    end = start / FPS
    out = {}
    for key, items in marks.items():
        lines, n = ["WEBVTT", ""], 0
        for i, (t, text) in enumerate(items):
            stop = items[i + 1][0] if i + 1 < len(items) else end
            if stop <= t:
                continue
            n += 1
            if key == "captions":
                lines += caption_cues(n, t, stop, text)
            else:
                lines += [str(n), f"{vtt_time(t)} --> {vtt_time(stop)}", text.replace("-->", "→").strip(), ""]
        out[key] = "\n".join(lines)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--site", type=Path, help="Film the built static site in this folder (the offline demo at /demo/) "
                    "instead of a running WIDER server.")
    ap.add_argument("--only", nargs="*", help="Film only these scenes again; the video joins them with the other scenes' earlier takes.")
    ap.add_argument("--no-encode", action="store_true")
    ap.add_argument("--scenes", type=Path, default=HERE / "scenes.py", help="The script of the video (a SCENES list).")
    ap.add_argument("--stage", type=Path, default=HERE / "stage.html", help="The stage page around the app.")
    ap.add_argument("--project", default="employee-voice",
                    help="Project folder name: paths ending in it are shown as ~/reviews/<name>.")
    ap.add_argument("--bitrate", type=int, default=2_500_000, help="Video bitrate in bits per second.")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("showcase_scenes", a.scenes.resolve())
    scenes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scenes)
    SCENES = scenes.SCENES
    _Proxy.routes = getattr(scenes, "ROUTES", {})

    rec = Recorder(a.out.resolve(), f"http://localhost:{a.port}/", a.stage.resolve(), a.project,
                   a.site.resolve() if a.site else None)
    names = [name for name, _ in SCENES]
    # a script may film in another order than the video shows (the result first, filmed once it exists)
    filming = getattr(scenes, "FILM_ORDER", names)
    await rec.start()
    try:
        for name in filming:
            if a.only and name not in a.only:
                continue
            print("scene", name, flush=True)
            rec.begin_take(name)
            await dict(SCENES)[name](rec)
            rec.end_take()
    finally:
        await rec.stop()
    manifest, seconds, missing = rec.write_manifest(names)
    for path in rec.write_tracks(names, a.out):
        print("wrote", path, flush=True)
    print(f"{rec.n} screenshots taken, {seconds:.0f} s of video" + (f"; no take yet of {', '.join(missing)}" if missing else ""))
    if not a.no_encode:
        subprocess.run(["swift", str(HERE / "encode.swift"), str(manifest), str(a.out / "wider-showcase.mp4"), str(FPS),
                        str(a.bitrate)],
                       check=True)


if __name__ == "__main__":
    asyncio.run(main())
