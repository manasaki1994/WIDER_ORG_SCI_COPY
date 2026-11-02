"""The demo answers the app's requests and lets nothing leave the page (openspec change interactive-demo, D2, D9)."""
import importlib.util
import json

from tests.unit.demo_node import DEMO_DIR, SNAPSHOT, run_demo

BLOCKED = "Blocked in the demo: nothing leaves this page"


def test_outside_fetch_is_refused():
    out = run_demo("""
      boot();
      for (const u of ["https://example.org/x", "//example.org/x", "/static/app.js", "http://demo.test.evil/api/home"]) {
        try { await fetch(u); out[u] = "sent"; } catch (e) { out[u] = e.constructor.name + ": " + e.message; }
      }
      out.spy = spy.fetch;
    """)
    assert out.pop("spy") == 0
    assert set(out.values()) == {f"TypeError: {BLOCKED}"}


def test_other_channels_are_refused():
    out = run_demo("""
      boot();
      for (const name of ["XMLHttpRequest", "WebSocket", "EventSource"]) {
        try { new window[name]("https://example.org/x"); out[name] = "opened"; } catch (e) { out[name] = e.message; }
      }
      out.beacon = navigator.sendBeacon("https://example.org/x", "data");
      out.spy = spy.beacon;
    """)
    assert out == {"XMLHttpRequest": BLOCKED, "WebSocket": BLOCKED, "EventSource": BLOCKED, "beacon": False, "spy": 0}


def test_get_answers_from_snapshot():
    out = run_demo("""
      boot();
      out.home = await getJson("/api/home");
      const r = await fetch("/api/outputs/file?path=exports/x.csv");
      out.file = { status: r.status, type: r.headers.get("Content-Type"), text: await r.text() };
      out.full = await getJson("http://demo.test/api/home");
    """)
    assert out["home"] == {"status": 200, "json": SNAPSHOT["bodies"]["b1"]["json"]}
    assert out["file"] == {"status": 200, "type": "text/csv", "text": SNAPSHOT["bodies"]["b3"]["text"]}
    assert out["full"] == out["home"]


def test_recorded_snapshot_answers_every_page_call():
    """The real snapshot as the build writes it: the home page and the history list answer at every checkpoint."""
    spec = importlib.util.spec_from_file_location("demo_build", DEMO_DIR / "build_demo.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    coverage = json.loads(build.COVERAGE.read_text(encoding="utf-8"))
    js = build.snapshot_js(build.SNAPSHOT, coverage, "nobody-in-particular")
    out = run_demo("""
      boot();
      for (const name of Demo.order()) {
        Demo.setCheckpoint(name);
        out[name] = [(await getJson("/api/home")).json.projects[0].name, (await fetch("/api/history?limit=200")).status];
      }
    """, snapshot_js=js)
    assert out and set(map(tuple, out.values())) == {("employee-voice", 200)}


def test_unknown_route_is_not_part_of_the_demo():
    out = run_demo("""
      boot();
      out.get = await getJson("/api/nothing/here");
    """)
    assert out["get"] == {"status": 404, "json": {"detail": "Not part of the demo"}}


def test_unsimulated_write_shows_reason():
    out = run_demo("""
      boot();
      out.upload = await getJson("/api/import/upload", { method: "POST", body: "{}" });
      out.other = await getJson("/api/labels", { method: "post", body: JSON.stringify({ label: "include" }) });
    """)
    assert out["upload"] == {"status": 409, "json": {"detail": SNAPSHOT["not_in_demo"]["POST /api/import/upload"]}}
    assert out["other"] == {"status": 409, "json": {"detail": "This is not part of the demo."}}


def test_api_links_are_caught():
    """A link to an /api/ file is saved from the recording, never followed."""
    out = run_demo("""
      boot();
      const a = document.createElement("a");
      a.setAttribute("href", "/api/outputs/file?path=exports/x.csv");
      a.setAttribute("download", "x.csv");
      document.body.appendChild(a);
      const e = a.click();
      out.prevented = e.defaultPrevented;
      out.blobs = await Promise.all(spy.blobs.map((b) => b.text()));
      out.left = document.body.children.filter((n) => n.getAttribute("data-demo-own")).length;
      const missing = document.createElement("a");
      missing.setAttribute("href", "/api/outputs/file?path=exports/none.csv");
      document.body.appendChild(missing);
      missing.click();
      out.notice = $("demo-notice").textContent;
      out.fetch = spy.fetch;
    """)
    assert out == {"prevented": True, "blobs": [SNAPSHOT["bodies"]["b3"]["text"]], "left": 0,
                   "notice": "Not part of the demo", "fetch": 0}


def test_outside_links_are_caught():
    out = run_demo("""
      globalThis.beforeBoot = (doc) => {
        const a = doc.createElement("a");
        a.setAttribute("href", "https://doi.org/10.0000/invented");
        a.setAttribute("id", "doi");
        doc.body.appendChild(a);
      };
      boot();
      const a = $("doi");
      out.href = a.getAttribute("href");
      out.kept = a.getAttribute("data-demo-href");
      out.prevented = a.click().defaultPrevented;
      out.notice = $("demo-notice").textContent;
      out.shown = !$("demo-notice").hidden;
      const later = document.createElement("a");
      later.setAttribute("href", "mailto:someone@example.org");
      document.body.appendChild(later);
      out.later = later.click().defaultPrevented;
      const inside = document.createElement("a");
      inside.setAttribute("href", "#rank");
      document.body.appendChild(inside);
      out.inside = inside.click().defaultPrevented;
    """)
    assert out["href"] == "#" and out["kept"] == "https://doi.org/10.0000/invented"
    assert out["prevented"] and out["later"] and not out["inside"]
    assert out["shown"] and "Links to outside sites are off in the demo" in out["notice"]


def test_notifications_are_off():
    out = run_demo("""
      boot();
      out.permission = Notification.permission;
      out.asked = await Notification.requestPermission();
      try { new Notification("done"); out.made = true; } catch (e) { out.made = false; }
    """)
    assert out == {"permission": "denied", "asked": "denied", "made": False}
