"""The "Model for query creation" page (model.js) under Node: the three guided steps on the first visit, the card grid
once a construct backend is set, choosing a card (construct + project default), and Check asking consent before
anything is sent. Synthetic backends only; nothing is contacted."""
import json
import re

from tests.unit.test_backends_js_node import PROVIDERS, REFUSAL, STATIC, common, model_page, out

OPEN_ROUTES = {"/api/stages", "/api/providers", "/api/embeddings"}
ROWS = [dict(p, locality=loc, destination=dest, last_check=None)
        for p, loc, dest in zip(PROVIDERS, ("cloud", "local"), ("api.openai.com (OpenAI)", "this computer"))]


def providers(construct=None):
    return {"providers": ROWS, "default": "gpt", "stage_providers": {"construct": construct} if construct else {},
            "stages": ["construct", "screen"], "kinds": ["openai_compat", "ollama", "anthropic", "cli_agent", "litellm"]}


def test_guided_steps_on_first_visit():
    r = model_page(out("{calls, html: $('#model-body').innerHTML}"), {"/api/providers": providers()})
    assert {c[0] for c in r["calls"]} <= OPEN_ROUTES and all(c[1] == "GET" for c in r["calls"])   # no detect, no probe
    html = r["html"]
    assert all(f'data-mstep="{s}"' in html for s in ("where", "which", "check"))
    assert html.count('data-where="') == 3 and 'aria-pressed="true"' not in html.split('data-mstep="which"')[0]
    assert "Nothing is sent anywhere until you press Check" in html and "Choose a model in step 2 first" in html
    assert 'id="backend-find"' in html and 'id="backend-add-hand"' in html


def test_choosing_a_card_sets_construct_and_default():
    """Steps: Choose only marks the card for step 3; "Use NAME for query creation" saves. Grid: Choose saves at once.
    Either way the stage route (construct) comes before the default route."""
    saved = [["/api/providers/stage", "POST", {"stage": "construct", "name": "ollama"}],
             ["/api/providers/default", "POST", {"name": "ollama"}]]
    ok = {"/api/providers/stage": {"ok": True}, "/api/providers/default": {"ok": True}}
    steps = model_page("await click('#model-body', '[data-pick]', (b) => b.dataset.pick === 'ollama');"
                       "const marked = calls.filter(c => c[1] === 'POST').length, html = $('#model-body').innerHTML;"
                       "await $('#model-use').onclick(); await flush();"
                       "replies['/api/providers'] = " + json.dumps(providers("ollama")) + "; await window.loadModel(); await flush();"
                       + out("{marked, html, posts: calls.filter(c => c[1] === 'POST'), msg: $('#model-msg').innerHTML,"
                             " later: $('#model-body').innerHTML}"),
                       {"/api/providers": providers(), **ok})
    assert steps["marked"] == 0 and 'id="model-use" data-name="ollama"' in steps["html"]
    assert "Use ollama for query creation" in steps["html"]
    assert steps["posts"] == saved
    assert "ollama is now the model for query creation and the project default" in steps["msg"]
    # with the choice saved, the page (and any later visit) shows the card grid with ollama marked
    assert "data-mstep" not in steps["later"]
    assert re.search(r'data-picker-card="ollama"(?:(?!data-picker-card).)*data-chosen="ollama"', steps["later"], re.S)
    grid = model_page("await click('#model-body', '[data-pick]', (b) => b.dataset.pick === 'ollama');"
                      + out("{posts: calls.filter(c => c[1] === 'POST'), html: $('#model-body').innerHTML}"),
                      {"/api/providers": providers("gpt"), **ok})
    assert grid["posts"] == saved
    assert "data-mstep" not in grid["html"] and "is\n        <b>gpt</b>" in grid["html"]


def test_check_asks_consent_first():
    """Check on the page goes through `api()` (never a bare fetch), and `api()` answers the probe route's consent
    refusal with the dialog before any probe is repeated: Cancel sends nothing more, Allow records consent and retries once."""
    page = model_page("await click('#model-body', '[data-pick]', (b) => b.dataset.pick === 'gpt');"
                      "await click('#model-body', '[data-pact]', (b) => b.dataset.pact === 'check');"
                      + out("{calls, result: $('#model-check-result').innerHTML}"),
                      {"/api/providers": providers(), "/api/providers/gpt/probe": {"ok": True, "answer": "pong", "locality": "cloud",
                                                                                    "elapsed_s": 0.4, "attempts": 1}})
    assert [c[:2] for c in page["calls"] if c[1] == "POST"] == [["/api/providers/gpt/probe", "POST"]]
    assert page["result"].startswith('<span class="ok">ok</span> 0.4s, 1 attempt')   # the result is shown in step 3
    assert "fetch(" not in (STATIC / "model.js").read_text(encoding="utf-8")


def test_grid_check_failure_names_its_error_once():
    """In the card grid the card's own last check line carries the error (from the server), so the result line under
    the card gives only failed, time and attempts; in the steps (no card line beside it) the error stays (M37)."""
    fail = {"ok": False, "elapsed_s": 0.11, "attempts": 1, "error": "HTTP 404: model not found"}
    page = model_page("await click('#model-body', '[data-pact]', (b) => b.dataset.pact === 'check' && b.dataset.name === 'gpt');"
                      + out("{result: $('[data-presult=\"gpt\"]').innerHTML}"),
                      {"/api/providers": providers(construct="gpt"), "/api/providers/gpt/probe": fail})
    assert page["result"] == '<span class="bad">failed</span> 0.11s, 1 attempt'
    page = model_page("await click('#model-body', '[data-pick]', (b) => b.dataset.pick === 'gpt');"
                      "await click('#model-body', '[data-pact]', (b) => b.dataset.pact === 'check');"
                      + out("{result: $('#model-check-result').innerHTML}"),
                      {"/api/providers": providers(), "/api/providers/gpt/probe": fail})
    assert page["result"] == '<span class="bad">failed</span> 0.11s, 1 attempt · HTTP 404: model not found'
    refusal = json.dumps(REFUSAL)
    cancel = common(f"""
answers.push([403, {refusal}]);
const p = api("/api/providers/gpt/probe", {{ method: "POST" }}).then(() => "ok", (e) => e.cancelled);
await press("cancel"); const res = await p;
""" + out("{res, fetched, dialogs: made.length}"))
    assert cancel["fetched"] == [["/api/providers/gpt/probe", "POST", None]] and cancel["dialogs"] == 1 and cancel["res"] is True
    allow = common(f"""
answers.push([403, {refusal}], [200, {{}}], [200, {{"ok": true}}]);
const p = api("/api/providers/gpt/probe", {{ method: "POST" }}); await press("allow"); const res = await p;
""" + out("{res, fetched}"))
    assert [f[0] for f in allow["fetched"]] == ["/api/providers/gpt/probe", "/api/providers/gpt/consent", "/api/providers/gpt/probe"]
    assert allow["res"] == {"ok": True}


def test_guided_steps_return_after_remove():
    """Once the construct backend is removed (`stage_providers` no longer names one), the page shows the steps again."""
    r = model_page("const grid = $('#model-body').innerHTML;"
                   "replies['/api/providers'] = " + json.dumps(providers()) + ";"
                   "await window.loadModel(); await flush();"
                   + out("{grid, steps: $('#model-body').innerHTML}"),
                   {"/api/providers": providers("gpt")})
    assert "data-mstep" not in r["grid"] and "data-picker-card" in r["grid"]
    assert all(f'data-mstep="{s}"' in r["steps"] for s in ("where", "which", "check"))


def test_construct_select_prefers_the_stage_backend():
    """The Concepts and synonyms backend select (`#art-provider`) preselects the construct backend, else the project
    default; a backend the researcher already picked stays picked."""
    from tests.unit.test_query_roles import js_function

    app, com = ((STATIC / f).read_text(encoding="utf-8") for f in ("app.js", "common.js"))
    fns = "\n".join([js_function(com, "function fillSelect("), js_function(app, "function providerSelect(")])
    cfg = {"providers": [{"name": "gpt", "model": "gpt-x"}, {"name": "ollama", "model": "qwen3:8b"}], "default_provider": "gpt"}
    r = model_page(f"""{fns}
const pick = (c, had) => {{ const s = $('#art-provider'); s.value = had || ''; providerSelect('#art-provider', c, null, 'construct'); return s.value; }};
const cfg = {json.dumps(cfg)};
""" + out("{stage: pick({...cfg, stage_providers: {construct: 'ollama'}}), none: pick({...cfg, stage_providers: {}}),"
          " gone: pick({...cfg, stage_providers: {construct: 'removed'}}), had: pick({...cfg, stage_providers: {construct: 'ollama'}}, 'gpt')}"))
    assert r == {"stage": "ollama", "none": "gpt", "gone": "gpt", "had": "gpt"}
    assert 'providerSelect("#art-provider", proj.config, "#art-propose", "construct")' in app
