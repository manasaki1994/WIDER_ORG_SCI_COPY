"""The demo's state and the app's own storage stay in this tab (openspec change interactive-demo, design D4, D7)."""
from tests.unit.demo_node import SNAPSHOT, run_demo


def test_reload_restores_state():
    out = run_demo("""
      boot();
      Demo.choose("free");
      out.reload = location.reloaded;
      out.replaced = location.replaced;
      boot();
      out.state = [Demo.state.mode, Demo.state.checkpoint];
      out.card = !!$("demo-start");
      out.home = (await getJson("/api/home")).json;
    """)
    assert out["reload"] == "/demo/#home" and out["replaced"] is None
    assert out["state"] == ["free", "two"] and out["card"] is False
    assert out["home"] == SNAPSHOT["bodies"]["b4"]["json"]


def test_reset_clears_state():
    out = run_demo("""
      boot();
      Demo.choose("free");
      boot();
      localStorage.setItem("wider.lastPage.k", "rank");
      confirmAnswer = false;
      out.declined = Demo.reset();
      out.kept = Demo.state.mode;
      confirmAnswer = true;
      out.reset = Demo.reset();
      out.asked = out.confirmed;
      out.session = [...session.m.keys()];
      out.reload = location.reloaded;
      out.replaced = location.replaced;
      boot();
      out.after = [Demo.state.mode, Demo.state.checkpoint, !!$("demo-start")];
    """)
    assert out["declined"] is False and out["kept"] == "free"
    assert out["reset"] is True
    assert out["asked"] == ["Start the demo again? Everything you did in the demo is cleared."] * 2
    assert out["session"] == []
    assert out["reload"] and out["replaced"] is None
    assert out["after"] == [None, "one", True]


def test_runs_without_storage():
    out = run_demo("""
      storageWorks = false;
      boot();
      out.store = Demo.appStoreOn();
      localStorage.setItem("wider.x", "1");
      out.read = localStorage.getItem("wider.x");
      Demo.choose("free");
      out.state = [Demo.state.mode, Demo.state.checkpoint];
      out.home = (await getJson("/api/home")).status;
      out.real = realLocal.length;
    """)
    assert out == {"store": True, "read": "1", "state": ["free", "two"], "home": 200, "real": 0}


def test_app_keys_do_not_persist():
    """The app's wider.* keys live in this tab's sessionStorage under the demo's prefix, never in localStorage."""
    out = run_demo("""
      realLocal.setItem("wider.reviewer", "Someone real");
      boot();
      out.on = Demo.appStoreOn();
      out.reviewer = localStorage.getItem("wider.reviewer");
      localStorage.setItem("wider.lastPage.k", "rank");
      out.length = localStorage.length;
      out.keys = [localStorage.key(0), localStorage.key(1)];
      out.session = [...session.m.keys()].filter((k) => k.startsWith("wider-demo-app.")).sort();
      out.real = [...realLocal.m.entries()];
    """)
    assert out["on"] is True
    assert out["reviewer"] == "Reviewer A"
    # the name and the panel tab are pinned, not stored (demo-chapters-and-video D5, D6); the panel starts closed
    assert out["length"] == 2 and out["keys"] == ["wider.guide", "wider.lastPage.k"]
    assert out["session"] == ["wider-demo-app.wider.guide", "wider-demo-app.wider.lastPage.k"]
    assert out["real"] == [["wider.reviewer", "Someone real"]]



def test_exit_clears_app_keys_when_the_browser_refuses_the_tab_store():
    """D7: a browser that keeps its own localStorage gets the app's wider.* keys removed on Exit, not only on Reset."""
    out = run_demo("""
      realLocal.setItem("other.site", "kept");
      globalThis.beforeBoot = () => Object.defineProperty(globalThis, "localStorage",
        { configurable: false, writable: false, value: realLocal });
      boot();
      out.on = Demo.appStoreOn();
      localStorage.setItem("wider.reviewer", "Typed name");
      Demo.choose("free");
      out.exit = Demo.exit();
      out.real = [...realLocal.m.entries()];
    """)
    assert [out["on"], out["exit"], out["real"]] == [False, True, [["other.site", "kept"]]]

def test_free_starts_at_last_checkpoint():
    out = run_demo("""
      boot();
      Demo.choose("free");
      out.free = Demo.state.checkpoint;
      boot();
      Demo.choose("guided");
      out.guided = [Demo.state.checkpoint, Demo.state.step];
    """)
    assert out == {"free": "two", "guided": ["one", 0]}


def test_switching_mode_keeps_place():
    out = run_demo("""
      boot();
      Demo.choose("guided");
      Demo.state.step = 3; Demo.save();
      $("demo-free").click();
      out.free = [Demo.state.mode, Demo.state.checkpoint, Demo.state.step,
                  $("demo-free").getAttribute("aria-pressed"), $("demo-guided").getAttribute("aria-pressed")];
      $("demo-guided").click();
      out.guided = [Demo.state.mode, Demo.state.step];
    """)
    assert out == {"free": ["free", "one", 3, "true", "false"], "guided": ["guided", 3]}


def test_reviewer_fixed_whatever_is_stored():
    """demo-chapters-and-video D6: the demo works as Reviewer A whatever is written to the name key; a history call
    with any reviewer name still gets Reviewer A's recording (the name is a pattern's parameter)."""
    out = run_demo("""
      boot();
      localStorage.setItem("wider.reviewer", "Someone Else");
      localStorage.setItem("wider.panelTab", "guide");
      out.pinned = [localStorage.getItem("wider.reviewer"), localStorage.getItem("wider.panelTab")];
      boot();
      out.reload = localStorage.getItem("wider.reviewer");
      out.own = await getJson("/api/history?limit=5&author=Reviewer%20A");
      out.other = await getJson("/api/history?author=" + encodeURIComponent("Someone Else") + "&limit=5");
      out.limit = (await fetch("/api/history?author=Reviewer%20A&limit=6")).status;
    """)
    body = SNAPSHOT["bodies"]["b2"]["json"]
    assert out["own"] == {"status": 200, "json": body}
    assert out["other"] == {"status": 200, "json": body}
    assert out["limit"] == 404
    assert out["pinned"] == ["Reviewer A", "ask"] and out["reload"] == "Reviewer A"
