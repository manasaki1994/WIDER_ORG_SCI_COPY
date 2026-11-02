"""guide-assistant 8.1: Ask WIDER's routes (D8, D14, D15): one question at a time as a job, the job row without the
question or the answer, the history entry with the CLI's fields, consent for one question carried by the
``X-Wider-Consent-Once`` header and never stored. Synthetic projects and the fake backend only."""
import json
import threading

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_assistant_engine import DEDUP_Q, _answer, _proj
from tests.unit.test_cli_assistant import _needs_consent
from tests.unit.test_rank_update import _wait
from wider.assistant import ask as A
from wider.assistant import store
from wider.cli import app as cli_app
from wider.core.project import Project
from wider.providers import fake
from wider.ui.app import create_app

H = {"X-Wider-Author": "ana"}
SECRET = "synthetic-secret-question"


def _client(p):
    return TestClient(create_app(p))


def _ask(c, question=DEDUP_Q, page="dedup", headers=None, **kw):
    return c.post("/api/assistant/ask", json={"question": question, "page": page, **kw}, headers={**H, **(headers or {})})


def test_job_row_holds_no_text(tmp_path, monkeypatch):
    """The job's stored label, params, result, message and error hold neither the question nor the answer, for an
    answered question and for a failed one."""
    p = _proj(tmp_path)
    c = _client(p)
    q = f"How does deduplication treat {SECRET} records?"
    r = _ask(c, q)
    assert r.status_code == 200, r.text
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done", j
    turn = store.turns(Project.load(p.root), "ana")[-1]
    assert turn["question"] == q

    def broken(req):
        raise RuntimeError(f"model echoed {SECRET}")
    _answer(monkeypatch, broken)
    j2 = _wait(c, _ask(c, q).json()["job_id"])
    assert j2["status"] == "failed" and SECRET not in (j2.get("error") or "")
    rows = Project.load(p.root).store.execute(
        "SELECT label, params, result, message, error FROM jobs WHERE kind='assistant'").fetchall()
    assert len(rows) == 2
    for row in rows:
        text = json.dumps([*row], default=str)
        assert SECRET not in text and turn["answer"] not in text, text
        assert row[0] == "Ask WIDER"


def test_routes_assistant_one_at_a_time(tmp_path, monkeypatch):
    """While a question runs, a second one is refused with 409 and the busy sentence; nothing is stored for it."""
    p = _proj(tmp_path)
    c = _client(p)
    gate, started = threading.Event(), threading.Event()
    orig = fake._assistant_answer

    def slow(req):
        started.set()
        gate.wait(10)
        return orig(req)
    _answer(monkeypatch, slow)
    job = _ask(c).json()["job_id"]
    assert started.wait(10)
    r = _ask(c)
    assert r.status_code == 409 and r.json()["detail"] == A.BUSY
    gate.set()
    assert _wait(c, job)["status"] == "done"
    assert len(store.turns(Project.load(p.root), "ana")) == 1


def test_routes_assistant_same_dict_as_cli(tmp_path):
    """The page's history entry for a turn carries the fields `wider assistant ask --json` prints, with the same
    values: answer, based_on, read, answered_by, show, drafts, notes."""
    p = _proj(tmp_path)
    out = CliRunner().invoke(cli_app, ["assistant", "ask", DEDUP_Q, "--page", "dedup", "--json", "--dir", str(p.root)],
                             env={"WIDER_REVIEWER": "ana", "COLUMNS": "200"})
    assert out.exit_code == 0, out.output
    cli = json.loads(out.output)
    c = _client(p)
    d = c.get("/api/assistant/history", headers=H).json()
    assert d["author"] == "ana" and len(d["turns"]) == 1
    page = d["turns"][0]
    for k in ("answer", "based_on", "read", "answered_by", "show", "drafts", "notes", "question", "page"):
        assert page[k] == cli[k], k
    assert page["read"].startswith("Read:") and page["answered_by"] == "Answered by b (m-b)"
    j = _wait(c, _ask(c).json()["job_id"])
    assert j["status"] == "done" and j["result"]["turn"] == 2
    assert c.get("/api/assistant/history", headers={"X-Wider-Author": "bo"}).json()["turns"] == []
    assert c.get("/api/assistant/history").json() == {"author": "", "turns": []}


def test_consent_once_not_stored(tmp_path, monkeypatch):
    """Without consent the question is refused with the 403 dialog payload (with the fingerprint) and nothing is
    stored; the retry with the header for that fingerprint is answered, and neither the consent nor the question and
    its answer is stored (the consent text promises this; inspection 1 Nov 2026): the job result carries the turn."""
    p = _proj(tmp_path)
    _needs_consent(monkeypatch)
    c = _client(p)
    r = _ask(c)
    assert r.status_code == 403, r.text
    req = r.json()["detail"]["backend_consent_required"]
    assert req["backend"] == "b" and req["fingerprint"] == "fp-b" and req["operation"] == "assistant"
    assert store.turns(Project.load(p.root), "ana") == []
    r = _ask(c, headers={"X-Wider-Consent-Once": "llm:b:assistant:fp-b"})
    assert r.status_code == 200, r.text
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done" and j["result"]["kept"] is False and j["result"]["turn"] == 0
    assert j["result"]["question"] and j["result"]["answer"]          # the page shows it without a stored turn
    q = Project.load(p.root)
    assert store.turns(q, "ana") == [] and "b" not in q.config.backend_consent
    assert c.get("/api/assistant/history").json()["turns"] == []
    assert _ask(c).status_code == 403                                   # nothing was remembered


def test_consent_once_refused_after_backend_change(tmp_path, monkeypatch):
    """A header naming a fingerprint the backend no longer has is ignored: 403 again, nothing sent or stored."""
    p = _proj(tmp_path)
    _needs_consent(monkeypatch)
    c = _client(p)
    r = _ask(c, headers={"X-Wider-Consent-Once": "llm:b:assistant:fp-old"})
    assert r.status_code == 403 and r.json()["detail"]["backend_consent_required"]["fingerprint"] == "fp-b"
    r = _ask(c, headers={"X-Wider-Consent-Once": "llm:b:screen:fp-b"})       # another operation's allowance
    assert r.status_code == 403
    q = Project.load(p.root)
    assert store.turns(q, "ana") == [] and not q.store.execute("SELECT * FROM llm_usage").fetchall()


def test_routes_refusals_forget_unhelpful(tmp_path):
    """Refusals answer 400 with the spec's sentence; unhelpful marks a turn (404 for an unknown one); forget deletes
    only this author's conversation; a refused question starts no job."""
    p = _proj(tmp_path)
    c = _client(p)
    assert _ask(c, "  ").status_code == 400
    r = c.post("/api/assistant/ask", json={"question": DEDUP_Q, "page": "dedup"})
    assert r.status_code == 400 and r.json()["detail"] == A.NO_AUTHOR
    assert _ask(c, page="nosuchpage").status_code == 400
    assert _ask(c, depth="deeper").status_code == 400
    assert not Project.load(p.root).store.execute("SELECT * FROM jobs").fetchall()
    _wait(c, _ask(c).json()["job_id"])
    _wait(c, _ask(c, headers={"X-Wider-Author": "bo"}).json()["job_id"])
    assert c.post("/api/assistant/unhelpful", json={"turn": 1}, headers=H).json()["unhelpful"] is True
    assert store.turns(Project.load(p.root), "ana")[0]["unhelpful"] is True
    assert c.post("/api/assistant/unhelpful", json={"turn": 9}, headers=H).status_code == 404
    assert c.delete("/api/assistant/history", headers=H).json() == {"deleted": 1}
    q = Project.load(p.root)
    assert store.turns(q, "ana") == [] and len(store.turns(q, "bo")) == 1


def test_routes_budget(tmp_path):
    """The budget route shows the default by place, sets a number and goes back to the default; out of range is
    400."""
    p = _proj(tmp_path)
    c = _client(p)
    d = c.get("/api/assistant/budget").json()
    assert d["value"] is None and d["budget"] == d["default"] == 3000 and d["min"] == 500
    assert c.post("/api/assistant/budget", json={"value": 8000}, headers=H).json()["budget"] == 8000
    assert c.post("/api/assistant/budget", json={"value": 100}, headers=H).status_code == 400
    assert c.post("/api/assistant/budget", json={"value": None}, headers=H).json()["value"] is None


def test_routes_stop_stores_nothing(tmp_path, monkeypatch):
    """Stop while the model is answering: the job ends cancelled and no turn is stored."""
    p = _proj(tmp_path)
    c = _client(p)
    gate, started = threading.Event(), threading.Event()
    orig = fake._assistant_answer

    def slow(req):
        started.set()
        gate.wait(10)
        return orig(req)
    _answer(monkeypatch, slow)
    job = _ask(c).json()["job_id"]
    assert started.wait(10)
    assert c.post(f"/api/jobs/{job}/cancel", headers=H).status_code == 200
    gate.set()
    assert _wait(c, job)["status"] == "cancelled"
    assert store.turns(Project.load(p.root), "ana") == []


def test_indexing_message_while_index_built(tmp_path, monkeypatch):
    """With an embedding model and no meaning index yet, the running job's message is "Updating the index of WIDER's
    documents…" (the panel's wait line shows it); once the index exists the next question runs without it."""
    from wider.ui.routes_assistant import INDEXING

    p = _proj(tmp_path, embedding=True)
    c = _client(p)
    seen = []
    orig = A.ask

    def ask(q, *a, **kw):
        seen.append(q.store.execute("SELECT message FROM jobs WHERE kind='assistant' AND status='running'").fetchone())
        return orig(q, *a, **kw)
    monkeypatch.setattr(A, "ask", ask)
    assert _wait(c, _ask(c).json()["job_id"])["status"] == "done"
    assert _wait(c, _ask(c).json()["job_id"])["status"] == "done"
    assert [s[0] if s else None for s in seen] == [INDEXING, None]


def _cloud_cli(p):
    """Backend ``cc`` for query creation: a CLI agent (a cloud place, so it needs consent through the real ``assess``,
    no monkeypatch) whose command prints a synthetic answer."""
    import sys

    from wider.core.models import ProviderConfig
    ans = json.dumps({"answer": "Deduplication finds records of the same study.", "sources": [], "show": [], "drafts": []})
    q = Project.load(p.root)
    q.config.providers.append(ProviderConfig(name="cc", kind="cli_agent", model="m", timeout_s=30,
                                             command=[sys.executable, "-c", f"print({ans!r})"], extra={"output": "text"}))
    q.config.stage_providers["construct"] = "cc"
    q.save()
    q.close()


def test_consent_once_through_the_dialog_after_revoke(tmp_path):
    """M124 (1): with a cloud CLI backend, allowed with Remember and then revoked on Settings, a question gets the 403;
    the page's own dialog (common.js under Node, fed that 403 as it came) with Remember unticked and Allow sends the
    question once more with the header it builds; the server answers it, stores neither the turn nor the consent, and
    the next question asks again."""
    from tests.unit.a11y_dom import run_dom
    p = _proj(tmp_path)
    _cloud_cli(p)
    c = _client(p)
    assert c.post("/api/providers/cc/consent", json={"operations": ["assistant"]}).status_code == 200
    assert _wait(c, _ask(c).json()["job_id"])["status"] == "done"
    assert c.delete("/api/providers/cc/consent").status_code == 200
    first = _ask(c)
    assert first.status_code == 403, first.text
    sent = run_dom(f"""
      const fetched = [], answers = [[403, {json.dumps(first.json())}], [200, {{ job_id: "j" }}]];
      global.fetch = async (url, opts) => {{ fetched.push((opts && opts.headers) || {{}});
        const [status, body] = answers.shift(); return {{ ok: status < 400, status, statusText: "x", json: async () => body }}; }};
      const p = api("/api/assistant/ask", {{ method: "POST", body: "{{}}", headers: {{ "X-Wider-Author": "ana" }} }});
      let d; for (let i = 0; i < 20 && !(d = $("dialog.consent-dialog")); i++) await flush();
      d.querySelector("[data-remember]").checked = false;
      d.querySelector("[data-act=allow]").click();
      out({{ result: await p, headers: fetched }});
    """, scripts=("common.js",))
    assert sent["result"] == {"job_id": "j"} and len(sent["headers"]) == 2
    once = sent["headers"][1]["X-Wider-Consent-Once"]
    r = _ask(c, headers={"X-Wider-Consent-Once": once})
    assert r.status_code == 200, r.text
    assert _wait(c, r.json()["job_id"])["status"] == "done"
    q = Project.load(p.root)
    turns = store.turns(q, "ana")
    assert len(turns) == 1                                               # only the remembered-consent turn
    assert q.config.backend_consent == {}
    assert _ask(c).status_code == 403                                   # the next question asks again
