"""ui-redesign D17: what a run sends, beside its button. One builder (`wider/core/send_notice.py`) gives the sentence
the page fetches from GET /api/send-notice/<action> and the CLI prints before the run; locality comes from the backend's
configuration and is never assumed to be this computer. Synthetic projects only; nothing is sent."""
import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_notebook import _project
from wider.cli import app as cli_app
from wider.core import lookups
from wider.core.models import ProviderConfig
from wider.core.project import Project
from wider.core.send_notice import ACTIONS, notice, sentence
from wider.ui.app import create_app

runner = CliRunner()
WIDER = Path(__file__).resolve().parents[2] / "wider"


def _allow_on_21_oct(p):
    lookups.allow(p, author="ana")
    p.config.online_lookups.allowed_at = "2026-10-21T09:00:00+00:00"
    p.save()


def test_lookup_sentence_page_and_cli(tmp_path, monkeypatch):
    """Scenario "Abstract lookup": the sentence names the DOI going to OpenAlex, Crossref and Europe PMC in that order,
    the title of a record without a DOI going to OpenAlex, the contact address when set, why, and the consent date;
    `wider enrich` prints the same sentence before it starts; with Crossref only, both name Crossref only."""
    from wider.dedup.engine import run
    from wider.enrich import fetch

    p = _project(tmp_path)
    run(p)
    _allow_on_21_oct(p)
    p.config.protocol = p.config.protocol.set("enrichment.sources", ["openalex", "crossref", "europepmc"])
    p.save()
    s = TestClient(create_app(p)).get("/api/send-notice/enrich").json()["sentence"]
    assert "each record's DOI goes to OpenAlex, Crossref and Europe PMC in that order" in s
    assert "for a record without a DOI its title goes to OpenAlex" in s
    assert "a contact address goes along when one is set" in s
    assert "because ranking and screening read the abstract" in s and "WIDER_MAILTO" in s
    assert s.endswith("You allowed this on 21 Oct; change it in Settings.")
    sent = []
    monkeypatch.setattr(fetch, "enrich", lambda *a, **k: sent.append(1) or {"filled": 0})
    p.close()
    r = runner.invoke(cli_app, ["enrich", "--limit", "1", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert out.startswith(s) and sent == [1]   # the sentence comes before the run
    q = Project.load(p.root)
    q.config.protocol = q.config.protocol.set("enrichment.sources", ["crossref"])
    q.save()
    s2 = sentence(q, "enrich")
    assert "each record's DOI goes to Crossref;" in s2 and "OpenAlex, Crossref" not in s2 and "Europe PMC" not in s2
    q.close()
    r = runner.invoke(cli_app, ["enrich", "--limit", "1", "--dir", str(p.root)])
    assert " ".join(r.output.split()).startswith(s2)


def test_consent_states(tmp_path):
    """Scenario "No consent yet": without online lookups the sentence ends "WIDER asks before sending"; the
    prior-review lookups name their own switch."""
    p = _project(tmp_path)
    assert sentence(p, "enrich").endswith("WIDER asks before sending.")
    assert sentence(p, "seed_resolve").endswith("WIDER asks before sending.")
    assert "prior_reviews.allow_openalex_lookup" in sentence(p, "prior_resolve")
    _allow_on_21_oct(p)
    assert sentence(p, "enrich").endswith("You allowed this on 21 Oct; change it in Settings.")
    assert sentence(p, "term_yield").endswith("WIDER asks before sending.")   # its own switch, still off
    p.close()


@pytest.mark.parametrize("cfg, words", [
    (ProviderConfig(name="olla", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434"),
     "Where it runs is unknown until checked"),
    (ProviderConfig(name="lan", kind="openai_compat", model="m", base_url="http://192.168.1.20:8000/v1",
                    api_key_env="LAN_KEY"),
     "another computer on your network")])
def test_unknown_locality_never_local(tmp_path, cfg, words):
    """Scenario "Where a model runs": a loopback Ollama with unresolved routing is "unknown until checked", a private
    address is "another computer on your network"; both match the model cards' locality and neither says this
    computer."""
    from wider.core.backends import backend_place

    p = _project(tmp_path)
    p.config.providers = [cfg]
    p.config.default_provider = cfg.name
    p.save()
    n = TestClient(create_app(p)).get("/api/send-notice/screen").json()
    assert words in n["sentence"] and "this computer" not in n["sentence"]
    assert "the title and abstract of each record in scope" in n["sentence"]
    assert n["locality"] == backend_place(p, cfg)["locality"]
    assert n["sentence"].endswith("WIDER asks before sending.")
    p.close()


def _function_source(module: str, name: str) -> str:
    path = WIDER / f"{module}.py"
    text = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"no function {name} in wider/{module}.py")


SCAN = re.compile(r"\.complete\(|research_provider\(|require_backend_consent\(|lookup_client\(")
DEFINES = {"core/backend_consent.py", "core/lookups.py", "providers/base.py", "providers/registry.py",
           "cli_backend_consent.py", "core/send_notice.py"}


def test_every_sending_action_has_its_sentence(tmp_path):
    """Scenario "Every sending action has its sentence", its CLI and registry half: every action of the registry
    builds a sentence and its CLI commands print it before the run; a scan of wider/ for model calls, consent checks
    and lookup clients finds no call site missing from the registry; the prior-review lookups name their switch. The
    sentence beside each button is the shell's task 4.8."""
    p = _project(tmp_path)
    assert set(lookups.SERVICES) <= set(ACTIONS)
    for key, a in ACTIONS.items():
        n = notice(p, key)
        assert n["sentence"] and n["action"] == key, key
        for ref in a.cli:
            module, fn = ref.split(":")
            src = _function_source(module, fn)
            prints = f'say_sent(p, "{key}"' in src or (
                a.kind == "lookup" and f'"{a.purpose}"' in src and "ensure_consent(" in src)
            assert prints, f"{ref} does not print the sentence of {key}"
    sites = {s for a in ACTIONS.values() for s in a.sites}
    missing = []
    for f in sorted(WIDER.rglob("*.py")):
        rel = f.relative_to(WIDER).as_posix()
        text = f.read_text(encoding="utf-8")
        if rel in DEFINES or not SCAN.search(text):
            continue
        if rel.startswith("cli"):
            continue   # the CLI's consent checks; its prints are checked above
        if rel not in sites:
            missing.append(rel)
        for purpose in re.findall(r'lookup_client\(\w+, "(\w+)"', text):
            assert rel in ACTIONS[purpose].sites, (rel, purpose)
    assert not missing, f"call sites missing from the registry: {missing}"
    assert "prior_reviews.allow_openalex_lookup" in sentence(p, "prior_chain")
    assert "Update ranking" not in ACTIONS   # the embedding step of Update ranking is the embed action
    assert "rank/corpus.py" in ACTIONS["embed"].sites and "cli:rank_update" in ACTIONS["embed"].cli
    r = TestClient(create_app(p)).get("/api/send-notice/nosuch")
    assert r.status_code == 404
    p.close()


def test_every_action_has_its_sentence_beside_a_button():
    """Scenario "Every sending action has its sentence", its page half (task 4.8): every action of the registry has a
    `data-send="<action>"` element in the page files, which common.js fills from the route."""
    static = WIDER / "ui" / "static"
    text = "".join(f.read_text(encoding="utf-8") for f in sorted(static.glob("*.*")) if f.suffix in (".html", ".js"))
    missing = [k for k in ACTIONS if f'data-send="{k}"' not in text]
    assert not missing, f"no sentence beside the button of {missing}"
    assert "/api/send-notice/" in (static / "common.js").read_text(encoding="utf-8")


def test_check_on_an_embedding_card_names_the_embedding_model(tmp_path):
    """Check on an embedding model's card (and `wider embed probe`) words what it sends to that embedding model, never
    "no model is set"; a named chat model is still the chat model."""
    from wider.core.models import EmbeddingConfig

    p = _project(tmp_path)
    p.config.embeddings = [EmbeddingConfig(name="emb1", kind="fake", model="fake", dim=16)]
    p.save()
    n = TestClient(create_app(p)).get("/api/send-notice/backend_check?backend=emb1").json()
    assert "no model is set" not in n["sentence"] and n["backend"]
    assert "a short test message (no project text)" in n["sentence"]
    assert "no model is set" in sentence(p, "backend_check", "nosuch")
    # the halves the model cards show apart (owner, 29 Oct, D29): what Check sends once, each card's consent beside it
    assert n["general"] == ("Check sends a short test message (no project text), because the test shows that the model "
                            "answers and where it runs.")
    assert n["consent_line"] and n["consent_line"][:-1].lower() == n["consent"].lower() and n["consent_line"] in n["sentence"]
    p.close()


SEND_PAGE = r"""
const made = [];
function el(send, backend, onPage) {
  const e = { dataset: { send }, textContent: "", hidden: true, closest: (s) => (s === ".page" ? onPage : null) };
  if (backend) e.dataset.sendBackend = backend;
  made.push(e); return e;
}
const shown = { hidden: false }, away = { hidden: true };
const els = [el("enrich", null, shown), el("enrich", null, shown), el("backend_check", "emb 1", shown),
             el("screen", null, away), el("nosuch", null, null), el("backend_check", "emb 1", shown)];
els[5].dataset.sendPart = "consent_line";   // a model card's consent beside "Sends to" (D29)
global.document = { querySelectorAll: (s) => (s === "[data-send]" ? els : []) };
const asked = [];
global.api = async (u) => { asked.push(u); if (u.includes("nosuch")) throw new Error("404"); return { sentence: "S:" + u, consent_line: "C:" + u }; };
"""


def test_page_loader_fills_the_sentences():
    """common.js fetches each sentence once per action and backend, places it word for word, leaves a hidden page for
    later and shows nothing for an action the server does not know."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    common = (WIDER / "ui" / "static" / "common.js").read_text(encoding="utf-8")
    block = common[common.index("const SendNotes"):common.index("// Fill a <select>")]
    script = SEND_PAGE + block + """
(async () => { await SendNotes.fill(document);
  const first = asked.length; await SendNotes.fill(document); const second = asked.length;
  console.log(JSON.stringify({ asked, first, second, els: els.map(e => [e.textContent, e.hidden]) })); })()
  .catch(e => { console.error(e.stack || e); process.exit(1); });"""
    res = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert sorted(out["asked"]) == ["/api/send-notice/backend_check?backend=emb%201", "/api/send-notice/enrich",
                                    "/api/send-notice/nosuch"]
    assert out["first"] == out["second"] == 3            # filled once; a later fill leaves done notes alone
    assert out["els"][0] == out["els"][1] == ["S:/api/send-notice/enrich", False]
    assert out["els"][2] == ["S:/api/send-notice/backend_check?backend=emb%201", False]
    assert out["els"][3] == ["", True]                    # on a hidden page: filled when that page opens
    assert out["els"][4] == ["", True]                    # unknown action: nothing shown, never a guess
    assert out["els"][5] == ["C:/api/send-notice/backend_check?backend=emb%201", False]   # the asked half only


def test_api_says_in_words_when_wider_stops_answering():
    """Owner, 29 Oct: the Overview's Continue card showed the browser's bare "Failed to fetch" after the server had
    stopped. ``api()`` now throws a sentence that says WIDER is not answering and how to get it back."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    common = (WIDER / "ui" / "static" / "common.js").read_text(encoding="utf-8")
    script = ("global.window = global; global.document = { addEventListener() {}, querySelector() { return null; },"
              " querySelectorAll() { return []; } };"
              "global.fetch = async () => { throw new TypeError('Failed to fetch'); };\n" + common + """
api('/api/project/summary').then(() => console.log('{}'),
  e => console.log(JSON.stringify({ m: e.message, u: e.unreachable })));""")
    out = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out == {"m": "WIDER is not answering. Start WIDER again the way you started it, then reload this page.", "u": True}


LOCAL_PAGE = r"""
function el(send, backend, onPage, part) {
  const e = { dataset: { send }, textContent: "", hidden: true, closest: (s) => (s === ".page" ? onPage : null) };
  if (backend) e.dataset.sendBackend = backend;
  if (part) e.dataset.sendPart = part;
  return e;
}
const pageA = { hidden: false }, pageB = { hidden: false };
const els = [el("embed", null, pageA), el("screen", null, pageA), el("enrich", null, pageA),
             el("backend_check", "local1", pageA, "consent_line"), el("backend_check", "local1", pageA, "general"),
             el("backend_check", "cloud1", pageA, "consent_line"), el("embed", null, pageB)];
global.document = { querySelectorAll: (s) => (s === "[data-send]" ? els : []) };
const LOCAL = { locality: "local", allowed: true, sentence: "WIDER sends x to m, on this computer. No consent is needed for this place.",
                consent_line: "No consent is needed for this place.", general: "Check sends a test message." };
global.api = async (u) => u.includes("enrich") ? { locality: "cloud", allowed: false, sentence: "To find abstracts, WIDER sends DOIs to OpenAlex. WIDER asks before sending." }
  : u.includes("cloud1") ? { locality: "cloud", allowed: false, consent_line: "WIDER asks before sending.", general: "Check sends a test message." }
  : u.includes("screen") ? { ...LOCAL, locality: "test" } : LOCAL;
"""


def test_nothing_leaves_line_once_per_page():
    """UX review F12: where nothing leaves the computer the page says so once ("Runs on this computer; nothing leaves
    it."), a local model card adds no consent line, and every note for something that leaves keeps its full sentence
    (privacy: the consent wording is never shortened)."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    common = (WIDER / "ui" / "static" / "common.js").read_text(encoding="utf-8")
    block = common[common.index("const SendNotes"):common.index("// Fill a <select>")]
    script = LOCAL_PAGE + block + """
(async () => { await SendNotes.fill(document);
  console.log(JSON.stringify(els.map(e => [e.textContent, e.hidden]))); })()
  .catch(e => { console.error(e.stack || e); process.exit(1); });"""
    res = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    line = "Runs on this computer; nothing leaves it."
    assert out[0] == [line, False]                       # the first local note of the page
    assert out[1] == [line, True]                        # the test model's note on the same page: not repeated
    assert out[2] == ["To find abstracts, WIDER sends DOIs to OpenAlex. WIDER asks before sending.", False]
    assert out[3] == ["", True]                          # a local card's consent line: nothing to add
    assert out[4] == ["Check sends a test message.", False]
    assert out[5] == ["WIDER asks before sending.", False]   # a card that sends out keeps its consent line
    assert out[6] == [line, False]                       # another page says it once too


def test_ask_panel_local_line_names_its_subject():
    """Hand check M124 (31 Oct 2026): beside Ask WIDER's sentence for a model off this computer, the section search's
    local line said only "Runs on this computer; nothing leaves it.", which read as contradicting the sentence above.
    It now names what runs here; where the answer runs here too, the page's one local line covers both."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    common = (WIDER / "ui" / "static" / "common.js").read_text(encoding="utf-8")
    block = common[common.index("const SendNotes"):common.index("// Fill a <select>")]
    shell = (WIDER / "ui" / "static" / "shell.js").read_text(encoding="utf-8")
    m = re.search(r'data-send="assistant_embed" data-send-local-line="([^"]+)"', shell)
    assert m, "the Ask WIDER panel's section-search note names its subject"
    own = m.group(1)
    assert "nothing leaves" not in own
    script = r"""
function el(send, line) {
  const e = { dataset: { send }, textContent: "", hidden: true, closest: () => null };
  if (line) e.dataset.sendLocalLine = line;
  return e;
}
const OWN = """ + json.dumps(own) + r""";
let away = true;
const els = [el("assistant"), el("assistant_embed", OWN)];
global.document = { querySelectorAll: (s) => (s === "[data-send]" ? els : []) };
global.api = async (u) => u.includes("assistant_embed") ? { locality: "test", allowed: true, sentence: "x" }
  : away ? { locality: "cloud", allowed: true, sentence: "WIDER sends your question to the model claude-code-2." }
  : { locality: "local", allowed: true, sentence: "on this computer" };
""" + block + """
(async () => { await SendNotes.fill(document); const a = els.map(e => [e.textContent, e.hidden]);
  away = false; await SendNotes.fill(document, true); const b = els.map(e => [e.textContent, e.hidden]);
  console.log(JSON.stringify({ a, b })); })()
  .catch(e => { console.error(e.stack || e); process.exit(1); });"""
    res = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["a"] == [["WIDER sends your question to the model claude-code-2.", False], [own, False]]
    assert out["b"][0] == ["Runs on this computer; nothing leaves it.", False] and out["b"][1][1] is True


ASSISTANT_SENT = ("your question, this page's guide, matching sections of WIDER's documents, your project's settings "
                  "for those topics, the project's status (no record) and your earlier questions")


@pytest.mark.parametrize("cfg, place, free", [
    (ProviderConfig(name="olla", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434"),
     "on this computer", True),
    (ProviderConfig(name="lan", kind="openai_compat", model="m", base_url="http://192.168.1.20:8000/v1",
                    api_key_env="LAN_KEY"), "another computer on your network", False),
    (ProviderConfig(name="remote", kind="openai_compat", model="m", base_url="https://api.example.com/v1",
                    api_key_env="REMOTE_KEY"), "a remote service at", False),
    (ProviderConfig(name="olla2", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434"),
     "Where it runs is unknown until checked", False),
    (ProviderConfig(name="fk", kind="fake", model="fake"), "a test backend that sends nothing", True)])
def test_send_notice_assistant_per_place(tmp_path, cfg, place, free):
    """Scenario "The chat sentence for each place" (guide-assistant D8): for a checked loopback Ollama, a private
    network address, a remote service, an unknown place and the fake backend, the sentence of `assistant` says what
    Ask sends, names the place as the model cards do (backend_place), and says "no consent is needed for this place"
    for the first and last only. The model is the query-creation model (D1), never the default one."""
    from wider.core import backends
    from wider.core.backends import backend_place

    p = _project(tmp_path)
    p.config.providers = [cfg]
    p.config.default_provider = cfg.name
    p.save()
    assert "no model is set" in sentence(p, "assistant")   # a default model alone is not the query-creation model
    p.config.stage_providers = {"construct": cfg.name}
    p.save()
    if cfg.name == "olla":
        backends.record_probe(p, "llm", cfg, True, "local")   # the card's Check found it on this computer
    n = TestClient(create_app(p)).get("/api/send-notice/assistant").json()
    s = n["sentence"]
    assert f"WIDER sends {ASSISTANT_SENT} to " in s and place in s, s
    assert n["locality"] == backend_place(p, cfg)["locality"] and n["backend"] == cfg.name
    assert ("No consent is needed for this place." in s) is free
    if not free:
        assert s.endswith("WIDER asks before sending.") and "this computer" not in s
    p.close()


def test_send_notice_assistant_embed(tmp_path):
    """Scenario "The embedding sentence": with a remote default embedding backend, the sentence of `assistant_embed`
    says WIDER sends the question and its own documents (no project data) to that service and that consent is needed;
    an `assistant` consent for that embedding backend is what changes it."""
    from wider.core import backend_consent as bc
    from wider.core.models import EmbeddingConfig

    p = _project(tmp_path)
    e = EmbeddingConfig(name="oai", kind="openai", model="text-embedding-3-small", api_key_env="OAI_KEY")
    p.config.embeddings = [e]
    p.config.default_embedding = "oai"
    p.save()
    n = TestClient(create_app(p)).get("/api/send-notice/assistant_embed").json()
    assert n["kind"] == "embedding" and n["backend"] == "oai" and n["locality"] == "cloud"
    assert ("WIDER sends your question, and WIDER's own documents to build the index, again after they change; no "
            "project data to the embedding model oai") in n["sentence"]
    assert n["sentence"].endswith("WIDER asks before sending.")
    bc.allow_backend(p, "oai", ["research"], embedding=True)   # another operation's consent is not Ask's
    assert sentence(p, "assistant_embed").endswith("WIDER asks before sending.")
    bc.allow_backend(p, "oai", ["assistant"], embedding=True)
    assert "You allowed this on" in sentence(p, "assistant_embed")
    p.close()
