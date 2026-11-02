"""Backend setup on the page runs under Node on a stand-in (no browser): the preset cards of "Find AI models on this
computer", the Add and Edit form values, the model list (loaded on open only from a loopback host), the replace
confirmation, the cost ranges, the backend-check text, the consent card and dialog, and the sensitive header field.
The setup page itself (setup.js) runs on a stand-in document whose elements are made on demand and whose
``querySelectorAll`` finds ``data-*`` buttons in the HTML written to them, so clicks reach the real handlers."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")

def fmt_when_js() -> str:
    """common.js's fmtWhen as an assignment, so a harness that loads a page script without common.js formats times as
    the page does (accessibility-and-naming 4.4); a later `const fmtWhen` stub still shadows it."""
    src = (STATIC / "common.js").read_text(encoding="utf-8")
    i = src.index("function fmtWhen(")
    depth = 0
    for k in range(src.index("{", i), len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return f"global.fmtWhen = {src[i:k + 1]};"
    raise AssertionError("no end for fmtWhen")


# esc as common.js has it, so the escaping is real
ESC = r"""
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
"""
ESC += fmt_when_js() + "\n"
# the "Command line" fold of a card (common.js; C6 D5), taken from the page script itself
ESC += next(ln for ln in (STATIC / "common.js").read_text(encoding="utf-8").splitlines()
            if ln.startswith("const cmdFold = ")) + "\n"

# A document whose elements are made on demand: any property not set yet is another element (so form.kind.value or
# form.reset() work), and querySelectorAll('[data-x]') returns one element per tag carrying data-x in its innerHTML,
# with every data-* of that tag in its dataset. Requests go to `api`, which records them and answers from `replies`.
PAGE = r"""
const calls = [], listeners = {};
const camel = (s) => s.replace(/-([a-z])/g, (m, c) => c.toUpperCase());
function el(name) {
  const store = { innerHTML: "", textContent: "", value: "", hidden: false, disabled: false, checked: false, readOnly: false,
    dataset: {}, _name: name, _kids: {} };
  const self = new Proxy(() => {}, {
    get(t, p) {
      if (typeof p === "symbol" || p === "then" || p === "toJSON") return undefined;
      if (p in store) return store[p];
      if (p === "querySelectorAll") return (sel) => findAll(self, sel);
      if (p === "querySelector") return (sel) => document.querySelector(sel);
      if (p === "addEventListener") return (type, fn) => { (listeners[name + ":" + type] ||= []).push(fn); };
      if (p === "focus") return () => { document.activeElement = self; };
      return (store[p] = el(name + "." + String(p)));
    },
    set(t, p, v) { store[p] = v; return true; },
    apply() { return self; },   // a page's `typeof el === "function" ? el() : el` still reaches the element
  });
  return self;
}
// like the browser's NodeList: index, length, forEach and spreading, but no find/map/filter (a page that calls them
// throws in the browser, so it throws here too)
class NodeListLike {
  constructor(items) { items.forEach((x, i) => { this[i] = x; }); this.length = items.length; }
  item(i) { return this[i] ?? null; }
  forEach(fn, that) { Array.prototype.forEach.call(this, fn, that); }
  [Symbol.iterator]() { return Array.prototype.values.call(this); }
}
global.NodeListLike = NodeListLike;
function findAll(parent, sel) { return new NodeListLike(findAllArray(parent, sel)); }
function findAllArray(parent, sel) {
  const m = sel.match(/^\[([\w-]+)\]$/); if (!m) return [];
  const html = String(parent.innerHTML || ""), out = [];
  const re = new RegExp(`<[^>]*\\b${m[1]}(="[^"]*")?[^>]*>`, "g");
  let t, i = 0;
  while ((t = re.exec(html))) {
    const key = sel + "#" + i++;
    const kid = parent._kids[key] ||= el(parent._name + " " + key);
    const ds = {}; for (const a of t[0].matchAll(/data-([\w-]+)="([^"]*)"/g)) ds[camel(a[1])] = a[2].replace(/&quot;/g, '"').replace(/&amp;/g, "&");
    kid.dataset = ds; out.push(kid);
  }
  return out;
}
const els = {};
global.document = { body: el("body"), querySelector: (s) => (els[s] ||= el(s)), createElement: (t) => el("new " + t),
  addEventListener() {}, activeElement: null };
global.window = global; global.dispatchEvent = () => {}; global.CustomEvent = class { constructor(t) { this.type = t; } };
global.CSS = { escape: (s) => s };
global.askConfirm = async () => true;
const replies = {};
global.api = async (url, opts) => {
  calls.push([url, (opts && opts.method) || "GET", opts && opts.body ? JSON.parse(opts.body) : null]);
  for (const [k, v] of Object.entries(replies)) if (url === k || url.startsWith(k + "?")) return typeof v === "function" ? v(url, opts) : JSON.parse(JSON.stringify(v));
  throw new Error("no reply for " + url);
};
global.askChoice = async () => null;
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); };
const $ = (s) => document.querySelector(s);
const urls = () => calls.map((c) => c[0]);
const click = async (box, sel, pick) => { const b = [...$(box).querySelectorAll(sel)].find(pick || (() => true)); await b.onclick(); await flush(); };
"""

PRESETS = [
    {"id": "openai", "family": "cloud", "kind": "openai_compat", "label": "OpenAI API", "vendor": "OpenAI",
     "default_model": "gpt-x", "models": ["gpt-x", "gpt-y"], "base_url": "https://api.openai.com/v1",
     "api_key_env": "OPENAI_API_KEY", "command": None, "extra": {}, "timeout_s": 120, "experimental": False},
    {"id": "ollama", "family": "local", "kind": "ollama", "label": "Ollama", "vendor": "Ollama", "default_model": "",
     "models": [], "base_url": "http://127.0.0.1:11434", "api_key_env": None, "command": None, "extra": {},
     "timeout_s": 300, "experimental": False},
    {"id": "antigravity", "family": "cli", "kind": "cli_agent", "label": "Antigravity", "vendor": "Google",
     "default_model": "", "models": [], "base_url": None, "api_key_env": None, "command": ["agy", "-p"],
     "extra": {"output": "text"}, "timeout_s": 600, "experimental": True,
     "warning": "Experimental: <b>not</b> checked against its docs.",
     "telemetry": "This tool may send its own usage data to Google; not turned off."},
]
DETECT = [
    {"preset": "antigravity", "family": "cli", "label": "Antigravity", "found": False, "locality": "cloud",
     "destination": "Google servers", "billing": "your plan", "checked": "Runs: not yet run", "experimental": True,
     "warning": "Experimental: <b>not</b> checked against its docs.",
     "telemetry": "This tool may send its own usage data to Google; not turned off."},
    {"preset": "openai", "family": "cloud", "label": "OpenAI API", "found": True, "key_env": "OPENAI_API_KEY",
     "key_set": True, "locality": "cloud", "destination": "api.openai.com", "billing": "pay per token",
     "checked": "Runs: checked against the docs"},
    {"preset": "ollama", "family": "local", "label": "Ollama", "found": True, "server": "127.0.0.1:11434",
     "locality": "local", "destination": "this computer", "billing": "free", "checked": "Runs: checked"},
]
PROVIDERS = [
    {"name": "gpt", "kind": "openai_compat", "preset": "openai", "model": "gpt-x", "base_url": "https://api.openai.com/v1",
     "api_key_env": "OPENAI_API_KEY", "command": None, "extra": {"max_output_tokens": 4000, "headers": {"X-Org": "acme"}},
     "timeout_s": 90, "temperature": 0.0, "max_concurrency": 4, "price_input_per_mtok": 1.5, "price_output_per_mtok": 6,
     "price_cached_input_per_mtok": 0.15, "price_cache_write_per_mtok": None, "key_set": True, "default": True, "stages": []},
    {"name": "ollama", "kind": "ollama", "preset": "ollama", "model": "qwen3:8b", "base_url": "http://127.0.0.1:11434",
     "api_key_env": None, "command": None, "extra": {}, "timeout_s": 300, "key_set": False, "default": False, "stages": []},
]
EMBEDDINGS = [{"name": "minilm", "kind": "sentence_transformers", "model": "all-MiniLM-L6-v2", "dim": None,
               "batch_size": 32, "base_url": None, "api_key_env": None, "price_input_per_mtok": None, "downloaded": True},
              {"name": "oai-emb", "kind": "openai", "model": "text-embedding-3-small", "dim": 512, "batch_size": 64,
               "base_url": None, "api_key_env": "OPENAI_API_KEY", "price_input_per_mtok": 0.02, "key_set": True}]
CONSENT = [{"name": "gpt", "kind": "openai_compat", "embedding": False, "locality": "cloud", "destination": "api.openai.com",
            "needs_consent": True, "operations": ["screen", "models"], "stale": False, "changed": []},
           {"name": "other", "kind": "openai_compat", "embedding": False, "locality": "cloud", "destination": "x.example.test",
            "needs_consent": True, "operations": [], "stale": False, "changed": []},
           {"name": "ollama", "kind": "ollama", "embedding": False, "locality": "local", "destination": "this computer",
            "needs_consent": False, "operations": [], "stale": False, "changed": []}]


def run_node(script: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    script = f"(async () => {{\n{script}\n}})().catch(e => {{ console.error(e && e.stack || e); process.exit(1); }});"
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def backends(body: str) -> dict:
    """backends.js alone, with the real escaping."""
    return run_node(f"{ESC}\n{(STATIC / 'backends.js').read_text(encoding='utf-8')}\n{body}")


def model_page(body: str, replies: dict | None = None) -> dict:
    """model.js (the model page, which holds the backend form) on the stand-in page; ``body`` runs after loadModel()."""
    base = {"/api/providers": {"providers": PROVIDERS, "default": "gpt", "stage_providers": {}, "stages": ["construct", "screen"],
                               "kinds": ["openai_compat", "ollama", "anthropic", "cli_agent", "litellm"]},
            "/api/providers/presets": {"presets": PRESETS}, "/api/backends/detect": {"rows": DETECT},
            "/api/providers/ollama/models": {"models": ["qwen3:8b", "llama3:8b"]}}
    base.update(replies or {})
    code = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in ("backends.js", "pickers.js", "model.js"))
    return run_node(f"{ESC}\n{PAGE}\nObject.assign(replies, {json.dumps(base)});\n{code}\n"
                    f"await window.loadModel(); await flush();\n{body}")


FIND = "await $('#backend-find').onclick(); await flush();"


def edit_on_model_page(name: str) -> str:
    """Find, then "Edit <name>" on the found card of that backend's preset."""
    return FIND + f"await click('#backend-cards', '[data-edit-backend]', (b) => b.dataset.editBackend === '{name}');"


def out(expr: str) -> str:
    return f"console.log(JSON.stringify({expr}));"


# ------------------------------------------------------------------ 12.4

def test_cards_grouped_by_family():
    r = backends(out(f"{{html: Backends.renderPresetCards({json.dumps(DETECT)}, [])}}"))
    html = r["html"]
    assert html.index("<h3>CLI</h3>") < html.index("<h3>Cloud</h3>") < html.index("<h3>Local</h3>")
    assert html.index("Antigravity") < html.index("<h3>Cloud</h3>") < html.index("OpenAI API") < html.index("<h3>Local</h3>")
    assert "Sends to: api.openai.com" in html and "Sends to: this computer" in html
    assert "OPENAI_API_KEY set" in html and "server answers at 127.0.0.1:11434" in html
    assert 'data-preset-add="ollama"' in html and "data-nothing-found" not in html
    # CLI presets disclose the tool's own usage data on the card; a card without a note shows none
    assert "This tool may send its own usage data to Google; not turned off." in html
    assert html.count("data-telemetry") == 1


def test_form_values_from_preset():
    r = backends(out(f"Backends.formValuesFromPreset({json.dumps(PRESETS[0])}, [])"))
    assert r["title"] == "Add AI model" and r["mode"] == "add" and r["name"] == "openai" and r["preset"] == "openai"
    assert (r["kind"], r["model"], r["base_url"], r["api_key_env"], r["timeout_s"]) == \
        ("openai_compat", "gpt-x", "https://api.openai.com/v1", "OPENAI_API_KEY", 120)
    r = backends(out(f"Backends.formValuesFromPreset({json.dumps(PRESETS[2])}, [])"))
    assert json.loads(r["command"]) == ["agy", "-p"] and r["extra"] == {"output": "text"} and r["experimental"] is True


def test_form_names_how_a_cli_tool_signs_in():
    """A preset's `auth_hint` shows in the form as a sign-in line, not only under Advanced (M27); none without one."""
    cli = dict(PRESETS[2], extra={"auth_hint": "Synth CLI is not signed in: run `synth` in a terminal, or set SYNTH_KEY"})
    r = backends(out(f"Backends.formValuesFromPreset({json.dumps(cli)}, [])"))
    assert r["signin"] == "Sign-in: run `synth` in a terminal, or set SYNTH_KEY."
    assert backends(out(f"Backends.formValuesFromBackend({json.dumps(dict(PROVIDERS[0], extra=cli['extra']))})"))["signin"] \
        == r["signin"]
    assert backends(out(f"Backends.formValuesFromPreset({json.dumps(PRESETS[0])}, [])"))["signin"] == ""


def test_edit_form_shows_current_values():
    r = backends(out(f"Backends.formValuesFromBackend({json.dumps(PROVIDERS[0])})"))
    assert r["title"] == "Edit gpt" and r["mode"] == "edit" and r["name"] == "gpt"
    assert (r["model"], r["timeout_s"], r["max_concurrency"], r["temperature"]) == ("gpt-x", 90, 4, 0)
    assert (r["price_input_per_mtok"], r["price_output_per_mtok"], r["price_cached_input_per_mtok"],
            r["price_cache_write_per_mtok"]) == (1.5, 6, 0.15, "")
    assert r["extra"] == {"max_output_tokens": 4000, "headers": {"X-Org": "acme"}}
    # the page form shows them: Edit opens the form titled "Edit gpt" with the current values, max_output_tokens apart
    p = model_page(edit_on_model_page("gpt") + out(
        "{title: $('#backend-form-title').textContent, hidden: $('#backend-form-box').hidden, model: $('#backend-form').model.value,"
        " mot: $('#backend-form').max_output_tokens.value, cached: $('#backend-form').price_cached_input_per_mtok.value,"
        " ro: $('#backend-form').name.readOnly, extra: $('#backend-form').extra.value}"))
    assert p == {"title": "Edit gpt", "hidden": False, "model": "gpt-x", "mot": 4000, "cached": 0.15, "ro": True, "extra": ""}



def test_edit_form_shows_every_extra_line():
    """The author's hand check M27 (23 Oct 2026): the "Other settings" box showed 3 lines, so the sixth setting of a
    Gemini CLI backend sat below its edge and could not be removed. The box now opens tall enough for all of them."""
    extra = {"stdin": "user", "output": "gemini_json", "auth_exit_codes": [41], "fatal_exit_codes": [42],
             "retry_once_exit_codes": [53], "auth_hint": "run gemini"}
    rows = [dict(PROVIDERS[0], extra=extra), PROVIDERS[1]]
    listing = {"providers": rows, "default": "gpt", "stage_providers": {}, "stages": [], "kinds": ["openai_compat"]}
    p = model_page(edit_on_model_page("gpt") + out("{text: $('#backend-form').extra.value, rows: $('#backend-form').extra.rows}"),
                   {"/api/providers": listing})
    assert p["text"].count("\n") == 5 and "retry_once_exit_codes=[53]" in p["text"]
    assert p["rows"] == 7

def test_embedding_edit_form_shows_current_values():
    r = backends(out(f"Backends.embeddingFormValues({json.dumps(EMBEDDINGS[1])})"))
    assert r["title"] == "Edit oai-emb" and (r["model"], r["dim"], r["batch_size"], r["price_input_per_mtok"]) == \
        ("text-embedding-3-small", 512, 64, 0.02)
    from tests.unit.test_pickers_js_node import rank_picker

    p = rank_picker("await click('#rk-embeddings', '[data-pact]', (b) => b.dataset.pact === 'edit' && b.dataset.name === 'oai-emb');" + out(
        "{title: $('#embedding-form-title').textContent, batch: $('#embedding-form').batch_size.value, hidden: $('#embedding-form-box').hidden}"))
    assert p == {"title": "Edit oai-emb", "batch": 64, "hidden": False}


def test_cloud_models_not_loaded_on_open():
    r = backends(out(f"Backends.modelOptions(Backends.formValuesFromPreset({json.dumps(PRESETS[0])}, []), {json.dumps(PRESETS[0])})"))
    assert r["autoLoad"] is False and r["loadLabel"] == "Load models from OpenAI" and r["suggested"] == ["gpt-x", "gpt-y"]
    p = model_page(edit_on_model_page("gpt")
                   + out("{urls: urls(), label: $('#backend-load-models').textContent}"))
    assert not any("/models" in u for u in p["urls"]) and p["label"] == "Load models from OpenAI"


def test_models_auto_load_only_on_loopback():
    cases = {"http://127.0.0.1:11434": True, "http://localhost:1234/v1": True, "http://[::1]:8080": True,
             "http://192.168.1.5:11434": False, "https://localhost.example.test/v1": False}
    r = backends(out("Object.fromEntries(" + json.dumps(list(cases)) +
                     ".map(u => [u, Backends.modelOptions({kind: 'openai_compat', base_url: u, model: ''}, null)]))"))
    assert {u: v["autoLoad"] for u, v in r.items()} == cases
    assert r["http://192.168.1.5:11434"]["loadLabel"] == "Load models from 192.168.1.5"
    # an Ollama without a URL is the loopback default; the page loads its list on opening the Edit form
    p = model_page(edit_on_model_page("ollama")
                   + out("{urls: urls(), list: $('#backend-model-list').innerHTML}"))
    assert "/api/providers/ollama/models" in p["urls"] and "llama3:8b" in p["list"]


def test_nothing_found_note():
    rows = [{**r, "found": False} for r in DETECT]
    r = backends(out(f"{{none: Backends.renderPresetCards({json.dumps(rows)}, []), some: Backends.renderPresetCards({json.dumps(DETECT)}, [])}}"))
    assert "data-nothing-found" in r["none"] and "add an AI model by hand" in r["none"] and "WIDER installs nothing" in r["none"]
    assert "data-nothing-found" not in r["some"]
    assert r["none"].count("data-preset-add") == 3   # every card can still be added


def test_reset_fields_text():
    d = {"error": "provider_exists", "name": "gpt", "reset_fields": ["timeout_s", "price_input_per_mtok"]}
    r = backends(out(f"{{t: Backends.resetFieldsText({json.dumps(d)}), h: Backends.replaceDialogHtml({json.dumps(d)})}}"))
    assert r["t"].startswith("gpt already exists. Replacing it resets timeout_s, price_input_per_mtok")
    assert "Edit instead" in r["t"]
    for act in ("replace", "edit", "cancel"):
        assert f'data-act="{act}"' in r["h"]
    # on the page: Add over an existing name asks first; Cancel saves nothing, Replace sends replace: true
    conflict = ("(url, opts) => { const b = JSON.parse(opts.body); if (b.replace) return {name: b.name, reset_fields: []};"
                " const e = new Error('exists'); e.status = 409; e.detail = {error: 'provider_exists', name: b.name, reset_fields: ['timeout_s']}; throw e; }")
    listing = json.dumps({"providers": PROVIDERS, "default": "gpt", "stage_providers": {}, "stages": [], "kinds": ["ollama"]})
    body = (f"replies['/api/providers'] = (u, o) => (o && o.method === 'POST' ? ({conflict})(u, o) : {listing});"
            "let asked = []; global.askChoice = async (h) => { asked.push(h); return asked.length === 1 ? null : {act: 'replace'}; };"
            "await $('#backend-find').onclick(); await flush();"
            "await click('#backend-cards', '[data-preset-add]', (b) => b.dataset.presetAdd === 'ollama');"
            "const f = $('#backend-form'); f.name.value = 'ollama'; f.kind.value = 'ollama'; f.model.value = 'm';"
            "const posts = () => calls.filter(c => c[0] === '/api/providers' && c[1] === 'POST').map(c => !!c[2].replace);"
            "await f.onsubmit({preventDefault() {}}); await flush(); const afterCancel = posts();"
            "await f.onsubmit({preventDefault() {}}); await flush();"
            + out("{afterCancel, all: posts(), asked: asked.length}"))
    p = model_page(body)
    assert p["afterCancel"] == [False] and p["all"] == [False, False, True] and p["asked"] == 2


def test_estimate_text():
    r = {"priced": True, "min_usd": 0.12, "max_usd": 0.5, "calls": 40, "destination": "api.openai.com",
         "assumptions": ["output <b>200</b> tokens per call"],
         "lines": ["Estimated cost: $0.1200–$0.5000 for 40 calls to api.openai.com", "  assumption: output <b>200</b> tokens per call"]}
    out_ = backends(out(f"{{a: Backends.estimateText({json.dumps(r)}), b: Backends.estimateText({json.dumps({k: v for k, v in r.items() if k != 'lines'})})}}"))
    for h in out_.values():
        assert "$0.1200–$0.5000 for 40 calls to api.openai.com" in h and "&lt;b&gt;200&lt;/b&gt;" in h and "<b>200" not in h
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'showEstimate("#sc-estimate"' in app and 'stage: "construct"' in app


def test_usage_text_projection():
    js = (STATIC / "jobs.js").read_text(encoding="utf-8")
    fn = js[js.index("  window.usageText"):js.index("  window.usageChip")]
    u = {"calls": 5, "tokens": 1000, "cost_known": True, "cost_usd": 0.02, "cached_tokens": None,
         "cost_so_far_usd": 0.02, "projected_min_usd": 0.1, "projected_max_usd": 0.3}
    r = run_node(f"global.window = global;\n{fn}\n" + out(f"usageText({json.dumps(u)})"))
    assert r.endswith("cost so far $0.0200 · projected $0.1000–$0.3000")


def test_card_of_existing_backend():
    r = backends(out(f"{{html: Backends.renderPresetCards({json.dumps(DETECT)}, {json.dumps(PROVIDERS)}),"
                     f" add: Backends.formValuesFromPreset({json.dumps(PRESETS[1])}, {json.dumps(PROVIDERS)}).name}}"))
    assert 'data-edit-backend="ollama">Edit ollama</button>' in r["html"] and "Edit gpt" in r["html"]
    assert r["add"] == "ollama-2"
    p = model_page(FIND + "await click('#backend-cards', '[data-preset-add]', (b) => b.dataset.presetAdd === 'ollama');"
                   + out("{title: $('#backend-form-title').textContent, name: $('#backend-form').name.value}"))
    assert p == {"title": "Add AI model", "name": "ollama-2"}


def test_check_banner_text():
    r = backends(out("Backends.checkBannerText()"))
    assert "one fixed test request" in r and "no project data" in r and "may cost a little" in r
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    # Settings says it; the home banner is gone (Home has no backend banner)
    assert shell.count("Backends.checkBannerText()") == 1 and "two fixed test sentences" not in shell


def test_detect_not_run_on_open():
    p = model_page("const before = urls().filter(u => u.includes('detect')).length;"
                   "await $('#backend-find').onclick(); await flush();" +
                   out("{before, after: urls().filter(u => u === '/api/backends/detect').length, cards: $('#backend-cards').innerHTML.includes('<h3>Local</h3>')}"))
    assert p == {"before": 0, "after": 1, "cards": True}
    # Settings only sends the researcher to the model page and presses the button there
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    assert "/api/backends/detect" not in shell and 'q("#backend-find")' in shell


def test_embed_estimate_confirm():
    est = {"priced": True, "min_usd": 0.01, "max_usd": 0.02, "calls": 3, "destination": "api.openai.com",
           "lines": ["Estimated cost: $0.0100–$0.0200 for 3 calls to api.openai.com"]}
    r = backends(f"""
const seen = [], asked = [];
const api = async (u, o) => {{ seen.push([u, JSON.parse(o.body)]); return {json.dumps(est)}; }};
const free = await Backends.confirmEmbedCost({json.dumps(EMBEDDINGS[0])}, api, async () => {{ throw new Error("asked"); }});
const cancel = await Backends.confirmEmbedCost({json.dumps(EMBEDDINGS[1])}, api, async (h) => {{ asked.push(h); return null; }});
const go = await Backends.confirmEmbedCost({json.dumps(EMBEDDINGS[1])}, api, async (h) => ({{act: "continue"}}));
""" + out("{free, cancel, go, seen, asked}"))
    assert r["free"] is True and r["cancel"] is False and r["go"] is True
    assert r["seen"] == [["/api/estimate", {"stage": "embed", "embedding": "oai-emb"}]] * 2
    assert "$0.0100–$0.0200" in r["asked"][0] and 'data-act="continue"' in r["asked"][0] and 'data-act="cancel"' in r["asked"][0]
    # "Embed corpus" asks before Jobs.start, and Cancel returns before it
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    h = app[app.index('$("#rk-embed").onclick'):]
    assert h.index("Backends.confirmEmbedCost(") < h.index("Jobs.start(") and 'Nothing started."; return; }' in h


# ------------------------------------------------------------------ 12.5

def test_experimental_card():
    r = backends(out(f"{{html: Backends.renderPresetCards({json.dumps(DETECT)}, [])}}"))
    card = r["html"][r["html"].index('data-card="antigravity"'):r["html"].index('data-card="openai"')]
    assert "Experimental</span>" in card and "Experimental: &lt;b&gt;not&lt;/b&gt; checked" in card
    p = model_page(FIND + "await click('#backend-cards', '[data-preset-add]', (b) => b.dataset.presetAdd === 'antigravity');"
                   + out("{ack: $('#backend-exp-ack').hidden, warn: $('#backend-exp-warning').textContent,"
                         " html: $('#backend-form-box').innerHTML.match(/<input type=\"checkbox\" name=\"accept_experimental\"[^>]*>/)[0]}"))
    assert p["ack"] is False and "not</b> checked" in p["warn"] and "checked" not in p["html"].replace("checkbox", "")



def test_cli_preset_without_a_model_is_refused_on_the_page():
    """The author's hand check M27 (23 Oct 2026): Save with an empty model on a CLI preset showed the API's refusal,
    which names a CLI command; the page says what to do on the form instead and sends nothing. The tick box sits
    beside its sentence (it used to be a full-width block alone on its line) and the sign-in line is body text."""
    p = model_page(FIND + "await click('#backend-cards', '[data-preset-add]', (b) => b.dataset.presetAdd === 'antigravity');"
                   "const f = $('#backend-form'); f.accept_experimental.checked = true; f.model.value = '';"
                   "await f.onsubmit({preventDefault() {}}); await flush();"
                   + out("{posts: calls.filter(c => c[0] === '/api/providers' && c[1] === 'POST').length,"
                         " msg: $('#backend-form-box').querySelector('#backend-form-msg').innerHTML,"
                         " box: $('#backend-form-box').innerHTML}"))
    assert p["posts"] == 0
    assert p["msg"] == '<span class="bad">Type a model id, or press the button under Model to load the list.</span>'
    assert 'id="backend-signin"' in p["box"] and '<p id="backend-signin"' in p["box"]
    assert '<span id="backend-form-msg" role="status">' in p["box"]
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "input[type=checkbox], input[type=radio] { display:inline-block; width:auto;" in css

def test_consent_revoke_card():
    r = backends(out(f"{{h: Backends.consentCardHtml({json.dumps(CONSENT)}, 'llm'), e: Backends.consentCardHtml([], 'embedding')}}"))
    h = r["h"]
    assert "screen, models" in h and 'data-revoke="gpt"' in h and "asks first" in h
    assert 'data-revoke="other"' not in h and "ollama" not in h and r["e"] == ""
    # Revoke lives on Settings only (the model page has no consent card); its DELETE is pinned in
    # test_settings_lists_backends_with_edit_and_revoke
    assert "data-revoke" not in (STATIC / "model.js").read_text(encoding="utf-8")



def test_settings_lists_backends_with_edit_and_revoke():
    """Settings lists the backends with edit buttons and, for each backend off this computer, its consented operations
    with "Revoke" (the DELETE route of `wider providers revoke` and `wider embed revoke`)."""
    provs = {"providers": PROVIDERS, "default": "gpt"}
    embs = {"embeddings": EMBEDDINGS, "default": "minilm"}
    econs = [{"name": "oai-emb", "kind": "openai", "embedding": True, "locality": "cloud", "destination": "api.openai.com",
              "needs_consent": True, "operations": ["research"], "stale": False, "changed": []}]
    r = backends(out(f"{{h: Backends.settingsBackendsHtml({json.dumps(provs)}, {json.dumps(embs)}, {json.dumps(CONSENT)}, "
                     f"{json.dumps(econs)})}}"))
    h = r["h"]
    for name, role in (("gpt", "LLM"), ("ollama", "LLM"), ("minilm", "embedding"), ("oai-emb", "embedding")):
        assert f'data-settings-edit="{name}" data-settings-role="{role}"' in h
    assert 'data-revoke="gpt" data-revoke-kind="llm"' in h and 'data-revoke="oai-emb" data-revoke-kind="embedding"' in h
    assert 'data-revoke="ollama"' not in h
    # an Ollama backend never checked reads "unknown, press Check" in its row and is not in the consent card (C3 12.2)
    unchecked = [dict(p, locality="unknown", locality_label="unknown, press Check") if p["name"] == "ollama" else
                 dict(p, locality="cloud") for p in PROVIDERS]
    u = backends(out(f"{{h: Backends.settingsBackendsHtml({json.dumps({'providers': unchecked, 'default': 'gpt'})}, null, [], [])}}"))["h"]
    ollama_row = u[u.index('<b>ollama</b>'):].split("</article>")[0]
    assert '<span class="chip loc-unknown" data-locality="unknown">unknown, press Check</span>' in ollama_row
    assert '<b>gpt</b> <span class="chip loc-cloud" data-locality="cloud">remote</span>' in u and "data-revoke" not in u
    # the Settings handler sends the same DELETE as the setup card, then reloads the page
    js = (STATIC / "shell.js").read_text(encoding="utf-8")
    assert 'qa("[data-revoke]", q("#settings-consent"))' in js and "/consent`, { method: \"DELETE\" }" in js

COMMON_PAGE = r"""
const made = [], fetched = [];
global.document = { body: { appendChild() {} }, activeElement: null, documentElement: null, addEventListener() {},
  createElement() { const d = { listeners: {}, innerHTML: "", setAttribute() {}, showModal() {}, close() {}, remove() {},
    addEventListener(t, f) { this.listeners[t] = f; }, querySelector() { return { focus() {} }; } }; made.push(d); return d; } };
global.window = global; global.setTimeout = () => 0; global.clearTimeout = () => {};
const answers = [];
global.fetch = async (url, opts) => { fetched.push([url, (opts && opts.method) || "GET", opts && opts.body ? JSON.parse(opts.body) : null]);
  const [status, body] = answers.shift(); return { ok: status < 400, status, statusText: "x", json: async () => body }; };
const press = async (act) => { for (let i = 0; i < 20 && !made.length; i++) await new Promise(r => setImmediate(r));
  made[made.length - 1].listeners.click({ target: { closest: () => ({ dataset: { act } }) } }); };
"""
REFUSAL = {"detail": {"message": "gpt sends text to api.openai.com.", "backend_consent_required": {
    "backend": "gpt", "kind": "llm", "operation": "screen", "destination": "api.openai.com", "locality": "cloud",
    "sent": "titles and abstracts", "warning": "", "changed": []}}}


def common(body: str) -> dict:
    return run_node(f"{COMMON_PAGE}\n{(STATIC / 'common.js').read_text(encoding='utf-8')}\n{body}")


def test_backend_consent_dialog_retry_once():
    allow = common(f"""
answers.push([403, {json.dumps(REFUSAL)}], [200, {{}}], [403, {json.dumps(REFUSAL)}]);
const p = api("/api/jobs/screen", {{ method: "POST", body: "{{}}" }}).then(() => "ok", (e) => e.message);
await press("allow"); const res = await p;
""" + out("{res, fetched, dialogs: made.length, html: made[0].innerHTML}"))
    assert allow["fetched"] == [["/api/jobs/screen", "POST", {}], ["/api/providers/gpt/consent", "POST", {"operations": ["screen"]}],
                                ["/api/jobs/screen", "POST", {}]]
    assert allow["dialogs"] == 1 and allow["res"] == "gpt sends text to api.openai.com."   # retried once, then the refusal
    assert "api.openai.com" in allow["html"] and "titles and abstracts" in allow["html"] and "Allow and continue" in allow["html"]
    cancel = common(f"""
answers.push([403, {json.dumps(REFUSAL)}]);
const p = api("/api/jobs/screen", {{ method: "POST", body: "{{}}" }}).then(() => "ok", (e) => [e.message, e.cancelled]);
await press("cancel"); const res = await p;
""" + out("{res, fetched}"))
    assert cancel["fetched"] == [["/api/jobs/screen", "POST", {}]]
    assert cancel["res"] == ["Nothing was sent: gpt is not allowed for screen.", True]
    # hand check M124: the server's label says the use in words, so Cancel never shows the code word "assistant"
    ask = {"detail": {**REFUSAL["detail"], "backend_consent_required": {**REFUSAL["detail"]["backend_consent_required"],
                                                                       "operation": "assistant", "label": "answer questions in Ask WIDER"}}}
    worded = common(f"""
answers.push([403, {json.dumps(ask)}]);
const p = api("/api/assistant/ask", {{ method: "POST", body: "{{}}" }}).then(() => "ok", (e) => e.message);
await press("cancel"); const res = await p;
""" + out("{res}"))
    assert worded["res"] == "Nothing was sent: you did not allow gpt to answer questions in Ask WIDER."
    emb = {"detail": {**REFUSAL["detail"], "backend_consent_required": {**REFUSAL["detail"]["backend_consent_required"],
                                                                       "backend": "oai-emb", "kind": "embedding", "operation": "embed",
                                                                       "sent": "titles and abstracts"}}}
    r = common(f"""
answers.push([403, {json.dumps(emb)}], [200, {{}}], [200, {{"ok": true}}]);
const p = api("/api/jobs/embed?embedding=oai-emb", {{ method: "POST" }}); await press("allow"); await p;
""" + out("{fetched, html: made[0].innerHTML}"))
    assert r["fetched"][1] == ["/api/embeddings/oai-emb/consent", "POST", {"operations": ["embed"]}]
    assert "embedding service oai-emb" in r["html"]


def test_sensitive_header_field():
    r = backends(out("{sens: Backends.headerFieldHtml('Authorization', '{env:MY_TOKEN}', 0), plain: Backends.headerFieldHtml('X-Org', 'acme', 1),"
                     " v1: Backends.headerValue('X-Api-Key', 'MY_KEY'), v2: Backends.headerValue('X-Org', 'acme'),"
                     " v3: Backends.headerValue('Cookie', '{env:C}'), bad: (() => { try { Backends.headerValue('X-Api-Key', 'sk-123 abc'); return null; } catch (e) { return e.message; } })()}"))
    assert 'value="MY_TOKEN"' in r["sens"] and "{env:" not in r["sens"] and "variable name only" in r["sens"]
    assert 'value="acme"' in r["plain"] and "variable" not in r["plain"]
    assert (r["v1"], r["v2"], r["v3"]) == ("{env:MY_KEY}", "acme", "{env:C}")
    assert "name of an environment variable" in r["bad"]


def test_load_models_on_hand_filled_form_sends_kind():
    """ui-tester F8: the by-hand form sent its kind as a preset id and got "unknown preset 'openai_compat'"."""
    p = model_page("await $('#backend-add-hand').onclick(); await flush();"
                   "$('#backend-form').base_url.value = 'http://127.0.0.1:8795/v1';"
                   "await $('#backend-load-models').onclick(); await flush();"
                   + out("{sent: calls.filter(c => c[0] === '/api/providers/models').map(c => c[2]),"
                         " list: $('#backend-model-list').innerHTML}"),
                   {"/api/providers/models": {"models": ["mock-small", "mock-large"]}})
    assert p["sent"] == [{"kind": "openai_compat", "base_url": "http://127.0.0.1:8795/v1"}]
    assert "mock-small" in p["list"]


def test_settings_screen_row_has_pass_selects(tmp_path):
    """Scenario: Settings screen row with a select per pass. The screen row shows "Pass A", "Pass B" and "Judge", the
    last two offering "Same as pass A" first; choosing `b` for Pass B sends the request `wider providers stage
    screen_b b` makes, and the route and the CLI leave the same stage overrides."""
    from fastapi.testclient import TestClient

    from tests.unit.test_setup_routes import _cli, _two_backends
    from tests.unit.test_shell_js_node import SETTINGS_ROUTES, settings_page
    from wider.core.project import Project
    from wider.ui.app import create_app

    ui = _two_backends(tmp_path / "ui")
    c = TestClient(create_app(ui))
    replies = {u: c.get(u).json() for u in SETTINGS_ROUTES}
    replies["/api/providers/stage"] = {"ok": True}
    body = r"""
const sels = [...$("#settings-backends").querySelectorAll("[data-settings-stage]")];
const html = $("#settings-backends").innerHTML;
const row = html.slice(html.indexOf('data-settings-stage-row="screen"'), html.indexOf('data-settings-stage-row="review"'));
const b = sels.find(s => s.dataset.settingsStage === "screen_b");
b.value = "b"; await b.onchange(); await flush();
console.log(JSON.stringify({ row, writes: calls.filter(c => c[1] !== "GET") }));
"""
    r = settings_page(body, replies, real_backends=True)
    row = r["row"]
    labels = [row.index(t) for t in ("pass A (first read)</label>", "pass B (second read)</label>", "judge (breaks a tie)</label>")]
    assert labels == sorted(labels)
    for stage in ("screen_b", "screen_judge"):
        sel = row[row.index(f'data-settings-stage="{stage}"'):].split("</select>")[0]
        options = sel.split("<option")[1:]
        assert options[0].startswith(' value="" selected>Same as pass A</option>')
        names = [p["name"] for p in replies["/api/providers"]["providers"]]
        assert [o.split('value="')[1].split('"')[0] for o in options[1:]] == names and {"a", "b"} <= set(names)
    a_sel = row[row.index('data-settings-stage="screen"'):].split("</select>")[0]
    assert "project default (a)" in a_sel
    assert r["writes"] == [["/api/providers/stage", "POST", {"stage": "screen_b", "name": "b"}]]
    # the same request on the route leaves what the CLI twin leaves
    assert c.post("/api/providers/stage", json=r["writes"][0][2]).status_code == 200
    cli = _two_backends(tmp_path / "cli")
    assert _cli("providers", "stage", "screen_b", "b", "--dir", cli.root).exit_code == 0
    assert Project.load(ui.root).config.stage_providers == Project.load(cli.root).config.stage_providers == {"screen_b": "b"}


def test_what_leaves_names_every_outbound_call():
    """Inspection 1 Nov 2026: Settings' "What leaves this computer" lists every call that can send text off this
    computer: the term and seed lookups, missing abstracts (with the contact address), prior reviews, and Ask WIDER on
    the query-creation model; with no model chosen it says the project default writes the proposals."""
    provs = {"providers": [{"name": "cloud1", "locality": "cloud", "destination": "a remote service"}],
             "stage_providers": {}, "default": "cloud1"}
    off = backends(out(f"Backends.whatLeavesHtml({json.dumps(provs)}, null, {{}}, {{}})"))
    for row in ("Search terms and whole queries to OpenAlex", "Seed papers to OpenAlex", "Prior-review studies",
                "Missing abstracts to OpenAlex", "Ask WIDER"):
        assert row in off, row
    assert "the project default; no model chosen on Model for query creation yet" in off
    assert "off: no search term or query is sent" in off and "no model chosen yet: nothing is sent" in off
    on = backends(out(f"Backends.whatLeavesHtml({json.dumps({**provs, 'stage_providers': {'construct': 'cloud1'}})},"
                      " null, {allow_openalex_lookup: true}, {allowed: true, terms_allowed: true})"))
    assert "with your contact address when WIDER_MAILTO is set" in on
    assert on.count("leaves this computer</span>") == 10  # construct, review, terms, seeds, prior, abstracts, 3 screen, ask
