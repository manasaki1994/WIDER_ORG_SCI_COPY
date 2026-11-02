"""The model picker cards (pickers.js) run under Node: the card fields, the chosen mark, and the cards drawn from the
rows `wider providers list --json` and `wider embed list --json` print. Synthetic backends only; no key value."""
import json
import re

from tests.unit.test_backends_js_node import EMBEDDINGS, ESC, PAGE, REFUSAL, STATIC, common, out, run_node
from wider.core.project import Project

COMMON = (STATIC / "common.js").read_text(encoding="utf-8")   # askChoice lives there (accessibility-and-naming D1)


def pickers(body: str) -> dict:
    """pickers.js alone, with the real escaping."""
    return run_node(f"{ESC}\n{(STATIC / 'pickers.js').read_text(encoding='utf-8')}\n{body}")


EMB_ROWS = [dict(EMBEDDINGS[0], default=True, locality="local", destination="this computer", last_check=None, size_mb=90,
                 downloaded=False),
            dict(EMBEDDINGS[1], default=False, locality="cloud", destination="api.openai.com (OpenAI)", last_check=None)]
EMB_REPLY = {"embeddings": EMB_ROWS, "default": "minilm", "kinds": ["openai", "openai_compat", "ollama", "sentence_transformers"]}


def rank_picker(body: str, replies: dict | None = None) -> dict:
    """The Ranking page's embedding picker (pickers.js) on the stand-in page; ``body`` runs after it is drawn."""
    base = {"/api/embeddings": EMB_REPLY}
    base.update(replies or {})
    code = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in ("backends.js", "pickers.js"))
    return run_node(f"{ESC}\n{PAGE}\nObject.assign(replies, {json.dumps(base)});\n{code}\n"
                    f"const picked = await Pickers.embeddingPicker($('#rk-embeddings')); await flush();\n{body}")


def cards(html: str) -> dict[str, str]:
    """The HTML of each card, by its backend name."""
    parts = re.split(r'(?=<div class="preset-card picker-card)', html)
    return {re.search(r'data-picker-card="([^"]*)"', p).group(1): p for p in parts if "data-picker-card" in p}


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


ROWS = [
    {"name": "a", "kind": "openai_compat", "model": "m-cloud", "api_key_env": "OPENAI_API_KEY", "key_set": False,
     "default": False, "stages": [], "locality": "cloud", "locality_source": "endpoint", "locality_label": "remote",
     "destination": "api.openai.com (OpenAI)", "last_check": None},
    {"name": "b", "kind": "ollama", "model": "qwen3:8b", "api_key_env": None, "key_set": False, "default": True,
     "stages": ["construct"], "locality": "local", "locality_source": "last_check", "locality_label": "this computer",
     "destination": "this computer",
     "last_check": {"at": "2026-10-15T08:30:00+00:00", "ok": True, "error": None, "via": "probe"}},
    {"name": "c", "kind": "ollama", "model": "llama3:8b", "api_key_env": None, "key_set": False, "default": False,
     "stages": [], "locality": "unknown", "locality_source": "not_checked", "locality_label": "unknown, press Check",
     "destination": "unknown (via Ollama)",
     "last_check": {"at": "2026-10-14T10:00:00+00:00", "ok": False, "error": "connection refused", "via": "check"}},
]


def test_card_fields_and_chosen_mark():
    r = pickers(f"console.log(JSON.stringify({{html: Pickers.cardsHtml({json.dumps(ROWS)}, {{kind: 'llm', chosen: 'b', "
                f"actions: ['check']}}), none: Pickers.cardsHtml([], {{kind: 'embedding'}})}}));")
    c = cards(r["html"])
    assert set(c) == {"a", "b", "c"}
    a, b, u = text(c["a"]), text(c["b"]), text(c["c"])
    assert "remote" in a and "api.openai.com" in a and "OPENAI_API_KEY MISSING" in a and "not checked yet" in a
    assert re.search(r"\bb local\b", b) and "default" in b and "construct" in b and "this computer" in b
    assert re.search(r"last check [^·]*2026[^·]*: ok", b) and "2026-10-15T" not in b   # local time (fmtWhen), not ISO
    assert "unknown, press Check" in u and "failed · connection refused" in u
    # D32 F15: the chosen card shows a "Chosen" chip (a span), the others a Choose button
    chosen = {n for n, h in c.items() if re.search(r'<span class="chip chip-chosen" data-chosen="[^"]*"[^>]*>Chosen</span>', h)}
    assert chosen == {"b"} and "data-pick=" not in c["b"] and "aria-pressed" not in r["html"]
    assert 'class="preset-card picker-card chosen"' in c["b"] and "chosen" not in c["a"].split(">")[0]
    assert all(re.search(r'<button type="button" class="secondary small-btn" data-pick=[^>]*>Choose</button>', c[n])
               for n in ("a", "c"))   # a real button: keyboard reaches it
    assert all('data-pact="check"' in h for h in c.values())
    assert "No embedding model is set up yet" in r["none"]


def test_card_values_are_escaped_and_key_set():
    rows = [dict(ROWS[0], name='x"<b>', model="<img src=x>", key_set=True)]
    r = pickers(f"console.log(JSON.stringify(Pickers.cardsHtml({json.dumps(rows)}, {{kind: 'llm', chosen: null}})));")
    assert "<img" not in r and "&lt;img src=x&gt;" in r and 'data-pick="x&quot;&lt;b&gt;"' in r
    assert "OPENAI_API_KEY set" in text(r)


def test_cards_match_providers_list(tmp_path, monkeypatch):
    """The cards drawn from `provider_rows` / `embedding_rows` (what `wider providers list --json` and `wider embed
    list --json` print, pinned equal in test_cli_setup) show each row's name, model, locality, destination, key state
    and last check."""
    from tests.unit.helpers import no_requests
    from wider.core.backends import add_embedding, add_provider, embedding_rows, provider_rows, record_probe

    monkeypatch.setenv("SYNTH_SET_KEY", "synthetic-key-value")
    monkeypatch.delenv("SYNTH_UNSET_KEY", raising=False)
    p = Project.init(tmp_path / "r")
    add_provider(p, name="a", kind="openai_compat", model="m1", base_url="https://api.openai.com/v1", api_key_env="SYNTH_UNSET_KEY")
    add_provider(p, name="s", kind="anthropic", model="m2", api_key_env="SYNTH_SET_KEY")
    cfg, _ = add_provider(p, name="o", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434", set_default=True)
    add_provider(p, name="u", kind="ollama", model="llama3:8b", base_url="http://127.0.0.1:11434")
    record_probe(p, "llm", cfg, True, "local")
    ecfg, _ = add_embedding(p, name="e", kind="openai_compat", model="emb-1", base_url="https://emb.example.invalid/v1",
                            api_key_env="SYNTH_UNSET_KEY")
    record_probe(p, "embedding", ecfg, False, "cloud", error="synthetic failure")
    no_requests(monkeypatch)
    llm, emb = provider_rows(p), embedding_rows(p)
    r = pickers(f"console.log(JSON.stringify({{llm: Pickers.cardsHtml({json.dumps(llm)}, {{kind: 'llm', chosen: 'o'}}), "
                f"emb: Pickers.cardsHtml({json.dumps(emb)}, {{kind: 'embedding', chosen: 'e'}})}}));")
    for rows, html in ((llm, r["llm"]), (emb, r["emb"])):
        c = cards(html)
        assert list(c) == [row["name"] for row in rows]
        for row in rows:
            h, t = c[row["name"]], text(c[row["name"]])
            assert row["model"] in t
            assert f'data-locality="{row["locality"]}"' in h
            if row["locality"] == "unknown":
                assert row["locality_label"] in t
            assert f"Sends to: {row['destination']}" in t
            if row["api_key_env"]:
                assert f"{row['api_key_env']} {'set' if row['key_set'] else 'MISSING'}" in t
            chk = row["last_check"]
            if chk:
                assert re.search(rf"last check [^·]*{chk['at'][:4]}[^·]*: {'ok' if chk['ok'] else 'failed'}", t)
                assert chk["at"][:16] not in t   # local time (fmtWhen), not the ISO string
                assert not chk["error"] or chk["error"] in t
            else:
                assert "not checked yet" in t
    o, u = text(cards(r["llm"])["o"]), text(cards(r["llm"])["u"])
    assert "local" in o and "unknown, press Check" in u and "SYNTH_SET_KEY set" in text(r["llm"])
    assert "synthetic-key-value" not in json.dumps(r)


def test_embedding_picker():
    """Opening Ranking draws the cards from one GET: no download, probe, detection or check route; there is no
    `rk-embedding` select any more. Choose sets the project's default embedding, which Embed corpus and Rank use."""
    r = rank_picker(out("{calls, picked, html: $('#rk-embeddings').innerHTML}"))
    assert r["calls"] == [["/api/embeddings", "GET", None]]
    assert r["picked"] == {"rows": EMB_ROWS, "chosen": "minilm"}
    c = cards(r["html"])
    assert set(c) == {"minilm", "oai-emb"} and 'data-chosen="minilm"' in c["minilm"] and 'data-pick="oai-emb"' in c["oai-emb"]
    assert 'data-pact="download"' in c["minilm"] and 'data-pact="download"' not in c["oai-emb"]
    assert "<span class=\"muted\" data-weights>not downloaded</span>" in c["minilm"] and "data-weights" not in c["oai-emb"]
    downloaded = rank_picker(out("$('#rk-embeddings').innerHTML"),
                             {"/api/embeddings": dict(EMB_REPLY, embeddings=[dict(EMB_ROWS[0], downloaded=True)])})
    assert 'data-pact="download"' not in downloaded
    for f in ("index.html", "app.js"):
        assert "rk-embedding\"" not in (STATIC / f).read_text(encoding="utf-8") and "#rk-embedding\"" not in (STATIC / f).read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "const e = rkEmbedding;" in app and "const emb = rkEmbedding," in app and "rkEmbedding = pick.chosen;" in app
    chose = rank_picker("await click('#rk-embeddings', '[data-pick]', (b) => b.dataset.pick === 'oai-emb');"
                        + out("{posts: calls.filter(c => c[1] !== 'GET'), msg: $('#embedding-msg').innerHTML}"),
                        {"/api/embeddings/default": {"ok": True}})
    assert chose["posts"] == [["/api/embeddings/default", "POST", {"name": "oai-emb"}]]
    assert "oai-emb is now the embedding model for Embed corpus and Rank" in chose["msg"]
    asked = rank_picker("global.askConfirm = async () => false;"
                        "await click('#rk-embeddings', '[data-pact]', (b) => b.dataset.pact === 'download');"
                        + out("calls.filter(c => c[1] !== 'GET')"))
    assert asked == []   # Download asks first; No sends nothing
    yes = rank_picker("global.askConfirm = async () => true;"
                      "await click('#rk-embeddings', '[data-pact]', (b) => b.dataset.pact === 'download');"
                      + out("calls.filter(c => c[1] !== 'GET')"), {"/api/embeddings/minilm/download": {"ok": True}})
    assert yes == [["/api/embeddings/minilm/download", "POST", None]]   # Yes downloads that model only


def test_embedding_check_asks_consent_first():
    """Check on a card goes through `api()` to the probe route, which refuses a backend off this computer without
    consent before sending anything; `api()` then asks in the dialog and records an embedding consent."""
    r = rank_picker("await click('#rk-embeddings', '[data-pact]', (b) => b.dataset.pact === 'check' && b.dataset.name === 'oai-emb');"
                    + out("{posts: calls.filter(c => c[1] !== 'GET'), cell: $('[data-presult=\"oai-emb\"]').innerHTML}"),
                    {"/api/embeddings/oai-emb/probe": {"ok": True, "dim": 512}})
    assert r["posts"] == [["/api/embeddings/oai-emb/probe", "POST", None]]
    assert "fetch(" not in (STATIC / "pickers.js").read_text(encoding="utf-8")
    emb = {"detail": {**REFUSAL["detail"], "backend_consent_required": {**REFUSAL["detail"]["backend_consent_required"],
                                                                       "backend": "oai-emb", "kind": "embedding", "operation": "probe"}}}
    cancel = common(f"""
answers.push([403, {json.dumps(emb)}]);
const p = api("/api/embeddings/oai-emb/probe", {{ method: "POST" }}).then(() => "ok", (e) => e.cancelled);
await press("cancel"); const res = await p;
""" + out("{res, fetched, dialogs: made.length}"))
    assert cancel == {"res": True, "fetched": [["/api/embeddings/oai-emb/probe", "POST", None]], "dialogs": 1}
    allow = common(f"""
answers.push([403, {json.dumps(emb)}], [200, {{}}], [200, {{"ok": true, "dim": 512}}]);
const p = api("/api/embeddings/oai-emb/probe", {{ method: "POST" }}); await press("allow"); const res = await p;
""" + out("{res, fetched}"))
    assert [f[:2] for f in allow["fetched"]] == [["/api/embeddings/oai-emb/probe", "POST"],
                                                 ["/api/embeddings/oai-emb/consent", "POST"], ["/api/embeddings/oai-emb/probe", "POST"]]
    assert allow["fetched"][1][2] == {"operations": ["probe"]} and allow["res"] == {"ok": True, "dim": 512}


def screen_picker(body: str, reply: dict, keep: str | None = None) -> dict:
    """The Screening page's model picker (pickers.js) on the stand-in page; ``body`` runs after it is drawn and
    ``got`` holds the rows its onPick received."""
    code = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in ("backends.js", "pickers.js"))
    return run_node(f"{ESC}\n{PAGE}\nreplies['/api/providers'] = {json.dumps(reply)};\n{code}\nconst got = [];\n"
                    f"const picked = await Pickers.llmPicker($('#sc-models'), {{stage: 'screen', keep: {json.dumps(keep)}, "
                    f"onPick: (r) => got.push(r && r.name)}}); await flush();\n{body}")


def test_screen_picker_preselects_stage_backend():
    """Opening Screening draws the cards from one GET (no detection, probe or check route) and marks the backend set
    for the screen stage, else the project default. Choosing another card only marks it for this run: nothing is
    saved. A backend picked earlier on the page stays picked; one that is gone falls back to the stage backend."""
    rows = [dict(r, default=r["name"] == "a", stages=["screen"] if r["name"] == "b" else []) for r in ROWS]
    reply = {"providers": rows, "default": "a", "stage_providers": {"screen": "b"}}
    r = screen_picker("const html0 = $('#sc-models').innerHTML;"
                      "await click('#sc-models', '[data-pick]', (b) => b.dataset.pick === 'c');"
                      + out("{calls, chosen: picked.chosen, row: picked.row.name, got, html0, html: $('#sc-models').innerHTML}"),
                      reply)
    assert r["calls"] == [["/api/providers", "GET", None]]   # one GET; choosing c sends nothing
    assert r["chosen"] == "b" and r["row"] == "b" and r["got"] == ["c"]
    marked = lambda html: [n for n, h in cards(html).items() if 'data-chosen="' in h]  # noqa: E731
    assert marked(r["html0"]) == ["b"] and marked(r["html"]) == ["c"]
    assert "changes no setting" in r["html"]
    assert screen_picker(out("picked.chosen"), reply, keep="c") == "c"
    assert screen_picker(out("picked.chosen"), reply, keep="removed") == "b"
    assert screen_picker(out("picked.chosen"), dict(reply, stage_providers={})) == "a"
    assert screen_picker(out("picked.chosen"), dict(reply, stage_providers={"screen": "removed"})) == "a"


def test_screen_run_line_names_locality_and_cost(tmp_path, monkeypatch):
    """The line next to Run names the picked backend, where the text goes and what it costs, from the rows `wider
    providers list --json` prints: a priced cloud backend, a local one, and an unpriced cloud one."""
    from typer.testing import CliRunner

    from tests.unit.helpers import no_requests
    from wider.cli import app as cli_app
    from wider.core.backends import add_provider, provider_rows, record_probe

    p = Project.init(tmp_path / "r")
    add_provider(p, name="priced", kind="openai_compat", model="m1", base_url="https://api.openai.com/v1",
                 api_key_env="SYNTH_UNSET_KEY", price_input_per_mtok=0.5, price_output_per_mtok=1.5)
    cfg, _ = add_provider(p, name="here", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434")
    record_probe(p, "llm", cfg, True, "local")
    add_provider(p, name="free", kind="openai_compat", model="m2", base_url="https://api.openai.com/v1",
                 api_key_env="SYNTH_UNSET_KEY")
    no_requests(monkeypatch)
    rows = provider_rows(p)
    cli = CliRunner().invoke(cli_app, ["providers", "list", "--json", "--dir", str(p.root)])
    assert cli.exit_code == 0 and json.loads(cli.output) == rows, cli.output
    by = {r["name"]: r for r in rows}
    r = pickers(f"const rows = {json.dumps(by)}, line = {{none: Pickers.runLine(null)}};"
                "for (const [n, x] of Object.entries(rows)) line[n] = Pickers.runLine(x);"
                "console.log(JSON.stringify(line));")
    dest = by["priced"]["destination"]
    assert r["priced"] == f"Runs on priced (m1): remote, sends to {dest}, $0.5 input and $1.5 output per million tokens."
    assert r["here"] == "Runs on here (qwen3:8b): local, no charge."
    assert r["free"] == f"Runs on free (m2): remote, sends to {by['free']['destination']}, not priced."
    assert r["none"] == "Choose a model above first."


def import_page(body: str, replies: dict | None = None) -> dict:
    """import.js (the Search results page's upload, sanity and final-mark code) on the stand-in page."""
    code = (STATIC / "import.js").read_text(encoding="utf-8")
    return run_node(f"{ESC}\n{PAGE}\nObject.assign(replies, {json.dumps(replies or {})});\n"
                    "let redrawn = 0; global.afterAction = async () => { redrawn++; }; global.loadImport = async () => {};\n"
                    f"{code}\n{body}")


def test_uploads_final_button():
    """"These uploads are final" asks first (No sends nothing), then posts `/api/import/final` (as `wider import final`)
    and shows the time of the mark instead of the button; a refusal shows its message as text, button kept."""
    at = "2026-10-15T09:00:00+00:00"
    r = import_page("showUploadsFinal(null); const open = {hidden: $('#imp-final').hidden, msg: $('#imp-final-msg').textContent};"
                    "global.askConfirm = async () => false; await $('#imp-final').onclick(); const declined = calls.length;"
                    "global.askConfirm = async () => true; await $('#imp-final').onclick();"
                    + out("{open, declined, calls, redrawn, hidden: $('#imp-final').hidden, msg: $('#imp-final-msg').textContent, "
                        f"when: fmtWhen({json.dumps(at)})}}"),
                    {"/api/import/final": {"at": at, "already": False, "change_id": 3}})
    assert r["open"] == {"hidden": False, "msg": ""} and r["declined"] == 0
    assert r["calls"] == [["/api/import/final", "POST", None]] and r["redrawn"] == 1
    assert r["hidden"] is True and "History" in r["msg"] and "A file added now reopens the uploads" in r["msg"]
    assert at not in r["msg"] and f"marked final on {r['when']}." in r["msg"]   # a readable time, not the stored ISO text
    why = "<b>no files imported yet</b>"
    refused = import_page("replies['/api/import/final'] = () => { throw new Error(" + json.dumps(why) + "); };"
                          "await $('#imp-final').onclick();"
                          + out("{hidden: $('#imp-final').hidden, text: $('#imp-final-msg').textContent, html: $('#imp-final-msg').innerHTML}"))
    assert refused == {"hidden": False, "text": why, "html": ""}   # set as text, never as markup
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="imp-final"' in html and "These uploads are final" in html
    assert "showUploadsFinal(d.final_at, d.files.length, d.files.filter(f => !f.imported).length)" in (STATIC / "app.js").read_text(encoding="utf-8")


# the uploads list of the Deduplication page (its "Records imported" box): test_dedup_page_node.py::test_dedup_uploads_card_is_the_records_imported_list


def test_hidden_set_note_rendered():
    """The Seed paper coverage card (app.js `showCoverage`) says the validation set is hidden while it is locked, names
    both sets once it opens, and says an early look was logged; only the development counts are drawn while locked."""
    from tests.unit.test_query_roles import js_function

    fn = set_cell_js() + js_function((STATIC / "app.js").read_text(encoding="utf-8"), "async function showCoverage(")
    row = {"benchmark_id": "b1", "method": "doi", "needs_confirmation": False, "score": 1.0, "title": "Synthetic paper"}
    dev = {"found": 1, "n": 1, "rows": [row]}
    replies = {
        "locked": {"validation_included": False, "validation_peeked": False, "scopes": {"alpha": {"development": dev}}},
        "open": {"validation_included": True, "validation_peeked": False,
                 "scopes": {"alpha": {"development": dev, "validation": {"found": 0, "n": 1, "rows": []}}}},
        "peeked": {"validation_included": True, "validation_peeked": True,
                   "scopes": {"alpha": {"development": dev, "validation": {"found": 0, "n": 1, "rows": []}}}}}
    body = f"""{fn}
const r = {{}};
for (const [k, v] of Object.entries({json.dumps(replies)})) {{
  replies["/api/coverage"] = v; $("#cov-val").checked = k !== "locked";
  await showCoverage("platforms"); r[k] = {{note: $("#cov-note").textContent, tables: $("#cov-tables").innerHTML}};
}}
r.urls = calls.map(c => c[0]);
""" + out("r")
    r = run_node(f"{ESC}\n{PAGE}\n{body}")
    assert r["locked"]["note"] == "Validation set hidden until the query is frozen or the uploads are marked final."
    assert "development 1/1" in r["locked"]["tables"] and "validation" not in r["locked"]["tables"]
    assert r["open"]["note"] == "Development and validation sets." and "validation 0/1" in r["open"]["tables"]
    assert r["peeked"]["note"].startswith("Validation set shown before the query was frozen") and "logged" in r["peeked"]["note"]
    assert r["urls"] == ["/api/coverage?scope=platforms", "/api/coverage?scope=platforms&validation=true",
                         "/api/coverage?scope=platforms&validation=true"]


def set_cell_js() -> str:
    """app.js's `setCell` and its constants (the set cell of a seed-paper listing), for an extracted function."""
    import re

    src = (STATIC / "app.js").read_text(encoding="utf-8")
    return "\n".join(re.search(rf"^const {name} = .*;$", src, re.M).group(0) for name in ("NOT_COUNTED", "USED", "USED_TITLE", "WAS_DEV_TITLE", "setCell")) + "\n"


def _card_run(fn: str, replies_js: str, act: str | None) -> dict:
    """showCoverage (app.js) with refine.js loaded, `askChoice` answering `act`; returns the GETs, the dialog HTML and
    the card's note (its HTML when it has lines)."""
    body = f"""{fn}
let shown = "";
global.askChoice = async (html) => {{ shown = html; return {json.dumps(act)} ? {{ act: {json.dumps(act)} }} : null; }};
{(STATIC / "refine.js").read_text(encoding="utf-8")}
replies["/api/coverage"] = {replies_js};
$("#cov-val").checked = true;
await showCoverage("platforms"); await flush();
""" + out("{gets: calls.map(c => c[0]), html: shown, note: $('#cov-note').innerHTML || $('#cov-note').textContent}")
    return run_node(f"{ESC}\n{PAGE}\n{body}")


def run_coverage_card(project, act: str | None) -> dict:
    """The coverage card's early look against the real route: the first reply comes from `GET /api/coverage` without a
    choice (records nothing); the card's second request, if any, is sent to the route as the card built it, and that
    reply is drawn. Returns the GETs, the dialog HTML and the note."""
    from fastapi.testclient import TestClient

    from tests.unit.test_query_roles import js_function
    from wider.ui.app import create_app

    fn = set_cell_js() + js_function((STATIC / "app.js").read_text(encoding="utf-8"), "async function showCoverage(")
    c = TestClient(create_app(Project.load(project.root)))
    first = c.get("/api/coverage?scope=platforms&validation=true").json()
    probe = _card_run(fn, f"(url) => url.includes('seen_choice') ? {{scopes: {{}}}} : {json.dumps(first)}", act)
    if len(probe["gets"]) < 2:
        return probe
    second = c.get(probe["gets"][-1]).json()
    res = _card_run(fn, f"(url) => url.includes('seen_choice') ? {json.dumps(second)} : {json.dumps(first)}", act)
    return {**res, "reply": second}


def test_coverage_card_asks_before_an_early_look(tmp_path):
    """The coverage card's confirm step on an early look (Q1): the asking notice with "Count them as seen" and "Keep
    them as validation", neither pre-selected, Cancel last; Cancel sends nothing more and records nothing."""
    from tests.unit.test_dry_run import _import_one, _proj

    p = _proj(tmp_path, n_val=2, name="card")
    _import_one(p, tmp_path, "card.ris")
    r = run_coverage_card(p, None)
    assert r["gets"] == ["/api/coverage?scope=platforms&validation=true"] and r["note"] == ""
    h = r["html"]
    assert "Validation set requested before the query was frozen; it holds 2 papers." in h
    assert 'data-act="count">Count them as seen' in h and 'data-act="keep">Keep them as validation' in h
    assert "checked" not in h and "autofocus" not in h and h.index('data-act="cancel"') > h.index('data-act="keep"')
    assert p.store.get_meta("validation_peeked_at") is None
    r = run_coverage_card(p, "count")
    assert r["gets"][-1] == "/api/coverage?scope=platforms&validation=true&seen_choice=count"
    assert r["note"].startswith("Validation set shown before the query was frozen; this look is logged in the history. "
                                "Counted as seen: the 2 validation papers shown no longer validate the search.")


def test_coverage_card_shows_the_validity_note():
    """After an unlock the card shows the validity note (D3): its sentence, then each line; the hidden-set note leads
    while the set is hidden; a used validation paper is "Val (used)" with its title, a former validation paper "Dev
    (former val)", a Validation paper counted with Development "Dev" with its title (validation-reuse D1)."""
    from tests.unit.test_query_roles import js_function

    fn = set_cell_js() + js_function((STATIC / "app.js").read_text(encoding="utf-8"), "async function showCoverage(")
    row = {"benchmark_id": "V001", "method": "none", "needs_confirmation": False, "score": 0.0, "title": "Synthetic"}
    dev = [{**row, "benchmark_id": "D001", "former": True}, {**row, "benchmark_id": "D002", "was_development": True}]
    note = {"sentence": "Search not validated: validation set seen on 2026-10-19 (1 unlock); no paper added since.",
            "lines": ["Looked at on 2026-10-19 while the query was reopened: 1 paper shown then counts as seen."]}
    reply = {"validation_included": True, "validation_peeked": False, "note": note,
             "scopes": {"alpha": {"Development": {"found": 0, "n": 2, "rows": dev},
                                  "Validation, used": {"found": 0, "n": 1, "rows": [row]}}}}
    body = f"""{fn}
replies["/api/coverage"] = {json.dumps(reply)};
await showCoverage("platforms"); const a = {{note: $("#cov-note").innerHTML, tables: $("#cov-tables").innerHTML}};
replies["/api/coverage"] = {json.dumps({**reply, "validation_included": False,
                                        "note": {"sentence": "Validation set hidden until the query is frozen or the uploads are marked final.",
                                                 "lines": [note["sentence"]]}})};
await showCoverage("platforms"); const b = {{note: $("#cov-note").innerHTML}};
""" + out("{a, b}")
    r = run_node(f"{ESC}\n{PAGE}\n{body}")
    assert r["a"]["note"] == f"{note['sentence']}<br>{note['lines'][0]}"
    t = r["a"]["tables"]
    assert ('<td title="used validation paper: seen before the query was revised or the uploads reopened; recall on it '
            'is not independent">Val (used)</td>') in t
    assert "<td>Dev (former val)</td>" in t
    assert '<td title="in Validation, but was in Development: counted with Development">Dev</td>' in t
    assert "seen (now development)" not in t
    assert r["b"]["note"] == f"Validation set hidden until the query is frozen or the uploads are marked final.<br>{note['sentence']}"


def test_validate_on_benchmark_confirm_step():
    """The Ranking page's "Check the seed papers" with the validation box ticked before the freeze (round 7, F1): the
    server answers `choice_needed`; the confirm shows "Count them as seen" and "Keep them as validation", neither
    pre-selected, Cancel last and focused (askChoice); Cancel sends nothing more, each button sends its seen_choice."""
    from tests.unit.test_query_roles import js_function

    src = (STATIC / "app.js").read_text(encoding="utf-8")
    handler = set_cell_js() + js_function(src, '$("#rk-validate").onclick = async () => {')
    handler += "\n" + js_function(src, "function pctTable(")
    handler += "\n" + js_function(src, "function rkSeedCardHtml(")
    notice = ("Validation set requested before the query was frozen; it holds 3 papers. Count them as seen: the papers "
              "shown no longer validate the search. Keep them as validation: they still count; every output states this "
              "choice. The choice is final.")
    done = {"summary": {"Validation": {"found": 3, "n": 3, "median_rank": 2, "recall_at_250": 1.0}}, "rows": [],
            "top_n_compare": {}, "validation_included": True, "notice": "validation set shown before the query was frozen"}

    def run(act):
        body = f"""
let shown = "";
global.askChoice = async (html) => {{ shown = html; return {json.dumps(act)} ? {{ act: {json.dumps(act)} }} : null; }};
{(STATIC / "refine.js").read_text(encoding="utf-8")}
replies["/api/rank/validate"] = (url) => url.includes("seen_choice") ? {json.dumps(done)} : {{choice_needed: true, notice: {json.dumps(notice)}, rows: [], summary: {{}}}};
$("#rk-ablate-val").checked = true;
{handler};
await $("#rk-validate").onclick(); await flush();
""" + out("{gets: calls.map(c => c[0]), html: shown, shown: $('#rk-validation').innerHTML}")
        return run_node(f"{ESC}\n{PAGE}\n{body}")

    r = run(None)
    assert r["gets"] == ["/api/rank/validate?validation=true"] and r["shown"] == ""
    h = r["html"]
    assert "Validation set requested before the query was frozen; it holds 3 papers." in h
    assert 'data-act="count">Count them as seen' in h and 'data-act="keep">Keep them as validation' in h
    assert "checked" not in h and "autofocus" not in h and h.index('data-act="cancel"') > h.index('data-act="keep"')
    assert 'dlg.querySelector("[data-act=cancel]").focus()' in COMMON[COMMON.index("function askChoice("):COMMON.index("function askConfirm(")]
    for act in ("count", "keep"):
        r = run(act)
        assert r["gets"] == ["/api/rank/validate?validation=true", f"/api/rank/validate?validation=true&seen_choice={act}"]
        assert "Seed paper recovery · Validation: 3/3 found" in r["shown"]


def test_ablation_checkbox_states_the_look():
    """The Ranking page's Ablation with "Include the validation set" ticked while the set is hidden (1.5c, scenario
    "Checkbox while the validation set is locked"): the box stays enabled and says that ticking it is a logged look
    (loadRank; drawn on the real page by test_stage_pages::test_page_ablation_with_validation); the server answers
    `choice_needed`; the confirm shows "Count them as seen" and "Keep them as validation", neither pre-selected,
    Cancel last and focused (askChoice); Cancel runs nothing, each button sends its seen_choice and the table shows
    the look notice."""
    from tests.unit.test_query_roles import js_function

    src = (STATIC / "app.js").read_text(encoding="utf-8")
    load = js_function(src, "async function loadRank(")
    assert '$("#rk-ablate-val").disabled = false;' in load
    assert ("Include the validation set: it is hidden until the query is frozen or the uploads are final; including it "
            "now is logged, and you choose whether the papers shown count as seen") in load
    handler = js_function(src, '$("#rk-ablate").onclick = async () => {')
    notice = ("Validation set requested before the query was frozen; it holds 2 papers. Count them as seen: they no "
              "longer validate the search; only papers added to Validation later can. Keep them as validation: they "
              "still count, and every output states that they were shown. The choice is final.")
    look = "validation set shown before the query was frozen; this look is logged in the history"
    done = {"rows": [{"embedding": "fake", "validation found": "2/2"}], "validation_peeked": True, "notice": look}

    def run(act):
        body = f"""
let shown = "";
global.askChoice = async (html) => {{ shown = html; return {json.dumps(act)} ? {{ act: {json.dumps(act)} }} : null; }};
global.afterAction = () => {{}};
{(STATIC / "refine.js").read_text(encoding="utf-8")}
replies["/api/rank/ablate"] = (url) => url.includes("seen_choice") ? {json.dumps(done)} : {{choice_needed: true, notice: {json.dumps(notice)}, rows: []}};
$("#rk-ablate-val").checked = true;
{handler};
await $("#rk-ablate").onclick(); await flush();
""" + out("{gets: calls.map(c => c[0]), html: shown, table: $('#rk-ablation').innerHTML}")
        return run_node(f"{ESC}\n{PAGE}\n{body}")

    r = run(None)
    assert r["gets"] == ["/api/rank/ablate?validation=true"] and r["table"] == ""
    h = r["html"]
    assert notice in h
    assert 'data-act="count">Count them as seen' in h and 'data-act="keep">Keep them as validation' in h
    assert "checked" not in h and "autofocus" not in h and h.index('data-act="cancel"') > h.index('data-act="keep"')
    assert 'dlg.querySelector("[data-act=cancel]").focus()' in COMMON[COMMON.index("function askChoice("):COMMON.index("function askConfirm(")]
    for act in ("count", "keep"):
        r = run(act)
        assert r["gets"] == ["/api/rank/ablate?validation=true", f"/api/rank/ablate?validation=true&seen_choice={act}"]
        assert f'<p class="small">{look}</p>' in r["table"] and "<th>validation found</th>" in r["table"]


# ----- the pass pickers of protocol and second-screen mode (screening-config-comparison 5.6, D14) -----
PASS_ROWS = [{"name": "a", "model": "a-m"}, {"name": "b", "model": "b-m"}, {"name": "c", "model": "c-m"}]


def pass_pickers(view: dict, picks: dict, a_name: str = "a") -> dict:
    return pickers(f"const view = {json.dumps(view)}, picks = {json.dumps(picks)};"
                   f"const html = Pickers.passPickersHtml({json.dumps(PASS_ROWS)}, view, picks);"
                   + out(f"{{html, fields: Pickers.passFields(view, picks, {json.dumps(a_name)})}}"))


def selected(html: str, p: str) -> str:
    sel = re.search(rf'<select data-pass-pick="{p}"[^>]*>(.*?)</select>', html, re.S).group(1)
    return re.search(r'<option value="([^"]*)" selected>', sel).group(1)


def test_screen_pass_pickers():
    """Protocol mode draws pass B and the judge, each with "Same as pass A" first and the stage default preselected;
    the picks go to the run as provider_b / provider_c ("Same as pass A" sends pass A's name, so a Settings default
    does not apply instead). In second-screen mode the labels name the reads S2 and S3. A pass that does not run has
    its picker disabled with the reason beside it and sends nothing."""
    r = pass_pickers({"mode": "protocol", "passes": {"A": "a"}, "pass_off": {}, "shared": []}, {"B": "b", "C": None})
    h = r["html"]
    assert "Pass B" in text(h) and "Judge" in text(h) and "(read S2)" not in h
    assert re.search(r'data-pass-pick="B"[^>]*><option value="">Same as pass A</option>', h)
    assert selected(h, "B") == "b" and selected(h, "C") == ""
    assert "change no setting" in text(h) and "disabled" not in h
    assert r["fields"] == {"provider_b": "b", "provider_c": "a"}
    s = pass_pickers({"mode": "second", "passes": {"A": "a"}, "pass_off": {}, "shared": []}, {"B": None, "C": "c"})
    assert "Pass B (read S2)" in s["html"] and "Judge (read S3)" in s["html"]
    assert s["fields"] == {"provider_b": "a", "provider_c": "c"}
    off = "the judge does not run: protocol main has one pass"
    o = pass_pickers({"mode": "protocol", "passes": {"A": "a"}, "pass_off": {"C": off}, "shared": []}, {"B": "b", "C": "c"})
    assert re.search(r'<select data-pass-pick="C" disabled aria-describedby="sc-pass-off-C">', o["html"])
    assert f'<span id="sc-pass-off-C" data-pass-off="C">{off}</span>' in o["html"]
    assert selected(o["html"], "C") == "" and o["fields"] == {"provider_b": "b"}


def test_screen_pickers_name_shared_model():
    """Passes that share a model are named in normal text (no muted or small class) below the pickers, the same
    sentence `wider screen protocol` prints; the text is escaped."""
    line = "Passes A and B use the same model (a-m, temperature 0): their readings are not independent."
    r = pass_pickers({"mode": "protocol", "passes": {"A": "a"}, "pass_off": {}, "shared": [line, "<b>x</b>"]},
                     {"B": None, "C": None})
    assert f"<p data-shared>{line}</p>" in r["html"]
    assert "<p data-shared>&lt;b&gt;x&lt;/b&gt;</p>" in r["html"]
    assert not re.search(r'<p class="[^"]*(muted|small)[^"]*" data-shared', r["html"])
    none = pass_pickers({"mode": "protocol", "passes": {"A": "a"}, "pass_off": {}, "shared": []}, {"B": "b", "C": "c"})
    assert "data-shared" not in none["html"]


TEST_ROW = {"name": "t", "kind": "fake", "model": "fake", "api_key_env": None, "key_set": False, "default": False,
            "stages": [], "locality": "test", "locality_source": "kind", "locality_label": "test",
            "destination": "this computer", "last_check": None}


def test_test_models_hidden_until_shown():
    """ui-redesign D32 F14 (owner 30 Oct): a test model that is not chosen is drawn hidden, with one "Show test models
    (N)" link after the cards; the link shows and hides it; a chosen test model is always shown; the pass pickers list
    a test model only when it is the pass's current pick."""
    rows = json.dumps(ROWS + [TEST_ROW])
    r = pickers(f"""
      const html = Pickers.cardsHtml({rows}, {{kind: 'llm', chosen: 'b'}});
      const chosenTest = Pickers.cardsHtml({rows}, {{kind: 'llm', chosen: 't'}});
      const el = (attrs) => ({{ hidden: attrs.hidden, attrs, getAttribute(k) {{ return this.attrs[k]; }},
                               setAttribute(k, v) {{ this.attrs[k] = v; }}, textContent: attrs.text || "" }});
      const card = el({{ hidden: true }}), btn = el({{ "aria-expanded": "false", text: "Show test models (1)" }});
      const box = {{ querySelectorAll: (s) => s === "[data-show-test]" ? [btn] : s === "[data-test-card]" ? [card] : [] }};
      Pickers.wire(box, () => {{}});
      btn.onclick(); const shown = [card.hidden, btn.textContent, btn.attrs["aria-expanded"]];
      btn.onclick(); const again = [card.hidden, btn.textContent, btn.attrs["aria-expanded"]];
      const pass = Pickers.passPickersHtml({rows}, {{mode: "protocol", passes: {{A: "a"}}, pass_off: {{}}, shared: []}}, {{B: null, C: null}});
      const passT = Pickers.passPickersHtml({rows}, {{mode: "protocol", passes: {{A: "a"}}, pass_off: {{}}, shared: []}}, {{B: "t", C: null}});
      console.log(JSON.stringify({{html, chosenTest, shown, again, pass, passT}}));""")
    c = cards(r["html"])
    assert re.search(r'<div class="preset-card picker-card" hidden data-test-card data-picker-card="t"', r["html"])
    assert all("data-test-card" not in c[n] for n in ("a", "b", "c"))
    assert r["html"].count("data-show-test") == 1 and "Show test models (1)</button>" in r["html"]
    assert "data-test-card" not in r["chosenTest"] and "data-show-test" not in r["chosenTest"]
    assert r["shown"] == [False, "Hide test models (1)", "true"] and r["again"] == [True, "Show test models (1)", "false"]
    assert 'value="t"' not in r["pass"] and 'value="t" selected' in r["passT"]
    assert r["passT"].count('value="t"') == 1   # only pass B's own pick lists it
