"""Tests asked for by the thirty-first round of C1 checks: the labelling queue's script runs whole on a fake page (the
read-only chip, the "not labelled yet" filter, the one name (common.js WiderName) that signs a decision), the
screening API refuses JSON true as a record limit, and Fetch asks for consent again through the real request."""
import json

from tests.unit.test_drawer_keys_node import STATIC, run_node

RECORDS = (STATIC / "records.js").read_text(encoding="utf-8")
COMMON = (STATIC / "common.js").read_text(encoding="utf-8")
JOBS = (STATIC / "jobs.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")

# a page where every selector answers with its own element, remembered by selector, and a fake localStorage
QUEUE_PAGE = r"""
const els = {};
const mk = (s) => ({ sel: s, value: "", textContent: "", innerHTML: "", hidden: false, dataset: {}, listeners: {},
  children: [{}, {}, {}, {}],
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
  fire(t) { (this.listeners[t] || []).forEach((fn) => fn({})); },
  focus() { document.activeElement = this; }, querySelector: () => null, querySelectorAll: () => [] });
global.document = { activeElement: null, querySelector: (s) => (els[s] = els[s] || mk(s)) };
global.window = { addEventListener() {} };
const mem = {};
global.localStorage = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); },
  removeItem: (k) => { delete mem[k]; } };
global.esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const calls = []; global.apiReply = () => ({});
global.api = (path, opts) => { calls.push([path, opts && opts.body ? JSON.parse(opts.body) : null]); return Promise.resolve(apiReply(path)); };
const opened = [];
global.Drawer = { open: (cfg) => opened.push(cfg), isOpen: () => false, current: () => null, close() {},
  bindRows() {}, markRow() {} };
const flush = () => new Promise((r) => setImmediate(r));
const $ = (s) => document.querySelector(s);
"""

NAME_BLOCK = COMMON[COMMON.index("// --- WiderName ---"):COMMON.index("// --- end WiderName ---")]


def queue_name(name: str | None = None) -> str:
    """common.js's real WiderName block for QUEUE_PAGE (ui-redesign D33): document events (records.js listens for
    "widername"), a counted reload and a name dialog that records its html and answers Cancel; ``name`` is stored before
    the block reads it, as a browser that already has one."""
    return r"""
const docEvents = {};
document.addEventListener = (t, f) => { (docEvents[t] = docEvents[t] || []).push(f); };
document.dispatchEvent = (e) => { (docEvents[e.type] || []).forEach((f) => f(e)); };
global.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
global.location = { reloads: 0, reload() { this.reloads++; } };
const dialogs = []; global.askChoice = (html) => { dialogs.push(html); return Promise.resolve(null); };
""" + (f'mem["wider.reviewer"] = {json.dumps(name)};\n' if name else "") + NAME_BLOCK


def test_the_labelling_queue_runs_whole_rows_filter_and_signing():
    out = run_node(QUEUE_PAGE + queue_name("ana") + RECORDS + r"""
const rows = [
  { record_id: 1, sample_id: "s", title: "a decided record", decision: "include", locked: false },
  { record_id: 2, sample_id: "s", title: "a closed record", decision: "", locked: true },
  { record_id: 3, sample_id: "s", title: "an open record", decision: "", locked: false } ];
apiReply = (p) => p.startsWith("/api/labels/queue") ? rows.map((r) => ({ ...r })) : p.startsWith("/api/labels") ? { change_id: 7 } : {};
const res = {};
// the page has no name field: the signing line names the one name WIDER works as
res.signing = $("#lb-signing").innerHTML;
// the queue loaded for ana, shown as "not labelled yet": both unlabelled records, the locked one too
$("#lb-filter").value = "unlabelled";
$("#lb-load").onclick(); await flush(); await flush();
const rowsOf = (html) => (html.match(/<tr data-idx[\s\S]*?<\/tr>/g) || []);
res.unlabelled = rowsOf($("#lb-table").innerHTML).map((r) => [/a decided|a closed|an open/.exec(r)[0], r.includes('<span class="chip">read-only</span>')]);
res.queueAsked = calls[0][0];
const cfg = opened[0];
res.drawer = [cfg.label, cfg.author, cfg.blind, "readOnly" in cfg];
// "all": every record, no read-only chip
$("#lb-filter").value = "all"; $("#lb-filter").onchange();
res.all = rowsOf($("#lb-table").innerHTML).map((r) => [/a decided|a closed|an open/.exec(r)[0], r.includes('<span class="chip">read-only</span>')]);
const body = { querySelectorAll: () => [], querySelector: () => ({ value: "" }) };
await cfg.decisions.find((d) => d.value === "maybe").fn(cfg.items[0], body);
res.signed = calls.filter(([p, b]) => p === "/api/labels" && b).map(([, b]) => [b.reviewer, b.record_id, b.decision]);
// switching to another name reloads the page, so no queue stays on screen under the new name
WiderName.set("bo"); res.reloads = location.reloads;
console.log(JSON.stringify(res));
""")
    assert out["signing"] == "Signing as <b>ana</b>."
    assert out["queueAsked"] == "/api/labels/queue?reviewer=ana"
    assert out["unlabelled"] == [["a closed", False], ["an open", False]]
    assert out["all"] == [["a decided", False], ["a closed", False], ["an open", False]]
    assert out["drawer"] == ["Labelling queue (blind)", "ana", True, False]
    assert out["signed"] == [["ana", 2, "maybe"]]
    assert out["reloads"] == 1


def test_fetch_after_a_cancelled_dialog_or_a_failed_start_asks_again_through_the_real_request():
    start = JOBS[JOBS.index("    async start(path"):]
    start = start[:start.index("\n    },\n") + 7]
    body = APP[APP.index("let enStarting = false;"):]
    body = body[:body.index("\n}\n") + 3]
    out = run_node(r"""
global.window = global;
global.document = { activeElement: null, addEventListener() {} };
global.setTimeout = () => 0; global.clearTimeout = () => {};
const sent = []; let reply = "consent";
global.fetch = async (path) => { sent.push(path);
  if (reply === "consent") return { ok: false, status: 403, statusText: "Forbidden",
    json: async () => ({ detail: { consent_required: { service: "OpenAlex" }, message: "allow?" } }) };
  return { ok: false, status: 500, statusText: "Server Error", json: async () => ({ detail: "the server stopped" }) }; };
""" + COMMON + r"""
const asked = []; askLookupConsent = async (info) => { asked.push(info.service || JSON.stringify(info)); return false; };
const call = api, callbacks = {}, active = {}, poll = async () => {};
const Jobs = {
""" + start + r"""
};
const els = {}; const $ = (s) => (els[s] ||= { textContent: "" });
const enLimit = () => 100, enBatch = 100, enRecords = () => {}, renderEnrich = () => {}, errors = [];
const showStageError = (k, m) => errors.push(m);
""" + body + r"""
(async () => {
  await startEnrich(false);
  const cancelled = [sent.splice(0), asked.length, els["#en-msg"].textContent, errors.splice(0)];
  reply = "fail"; await startEnrich(false);
  const failed = [sent.splice(0), asked.length, errors.splice(0)];
  reply = "consent"; await startEnrich(false);
  const again = [sent.splice(0), asked.length];
  console.log(JSON.stringify({ cancelled, failed, again }));
})();
""")
    assert out["cancelled"][:2] == [["/api/jobs/enrich?limit=100"], 1]     # one blocked request, the dialog opened
    assert out["cancelled"][2].startswith("Nothing was sent") and out["cancelled"][3] == []
    assert out["failed"][0] == ["/api/jobs/enrich?limit=100"] and out["failed"][1] == 1   # pressed again: sent again
    assert out["failed"][2] and "the server stopped" in out["failed"][2][0]
    assert out["again"] == [["/api/jobs/enrich?limit=100"], 2]            # after the failure, it asks again
