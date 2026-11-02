// "Compare configurations" card on the LLM screening page (change screening-config-comparison): backend, prompt
// version and temperature per row on the locked development sample, the estimate as a confirm step before Run, and the
// results in the order the rows were given, with no winner. The card calls the routes of `wider/ui/routes_compare.py`,
// which call the engine `wider screen compare run|show|list` calls. The builders return HTML strings only (every value
// through `esc`, common.js); the controller keeps the form across page reloads and is given its api and job starter,
// so tests run it under Node with fakes.
const Compare = (() => {
  // the sentences the engine (wider/screen/compare.py) gives word for word; a test compares them with the module
  const NOT_LOCKED = "sample {name} is not locked; lock it before comparing: wider sample lock {name}";
  const NOT_CORPUS = "These figures describe the development sample (records picked by hand), not the corpus.";
  const notLocked = (name) => NOT_LOCKED.split("{name}").join(name);
  const LOC = { local: "local", cloud: "remote", network: "local network", test: "Test model (not for real reviews)" };
  const locWord = (r) => LOC[r.locality] || r.locality_label || r.locality || "unknown";

  // one row as the CLI's --config text: NAME, NAME:pN, NAME@T or NAME:pN@T
  function configText(row) {
    const t = String(row.temperature ?? "").trim();
    return `${row.provider || ""}${row.prompt ? ":p" + row.prompt : ""}${t !== "" ? "@" + t : ""}`;
  }
  function body(form) {
    return { name: String(form.name || "").trim(), sample: form.sample || null, configs: form.rows.map(configText),
             repeats: Number(form.repeats), reuse_evaluations: !!form.reuse };
  }
  // the next free cmpN
  function nextName(comparisons) {
    const used = new Set((comparisons || []).map(c => c.name));
    let i = 1; while (used.has(`cmp${i}`)) i++;
    return `cmp${i}`;
  }

  // ---------------------------------------------------------------- form
  function rowHtml(row, i, n, providers, prompts) {
    const opt = (v, label, on) => `<option value="${esc(v)}"${on ? " selected" : ""}>${esc(label)}</option>`;
    const backends = providers.map(r => opt(r.name, `${r.name} · ${r.model || "?"} · ${locWord(r)}`, r.name === row.provider)).join("")
      || '<option value="">no model set up yet</option>';
    const vers = [opt("", "current prompt", !row.prompt), ...prompts.map(p => opt(p.version, `prompt v${p.version} (${p.status})`, String(p.version) === String(row.prompt)))].join("");
    return `<div class="row cmp-row" data-cmp-row="${i}">
      <span>${i + 1}.</span>
      <label>Model <select data-cmp-row-field="provider" data-i="${i}" aria-label="Model of configuration ${i + 1}">${backends}</select></label>
      <label>Prompt <select data-cmp-row-field="prompt" data-i="${i}" aria-label="Prompt of configuration ${i + 1}">${vers}</select></label>
      <label>Temperature <input data-cmp-row-field="temperature" data-i="${i}" size="5" inputmode="decimal" placeholder="model's"
        value="${esc(row.temperature ?? "")}" aria-label="Temperature of configuration ${i + 1} (empty: the model's setting)"></label>
      ${n > 1 ? `<button type="button" class="secondary small-btn" data-cmp-act="remove" data-i="${i}" aria-label="Remove configuration ${i + 1}">Remove</button>` : ""}
    </div>`;
  }
  function formHtml(st) {
    const f = st.form;
    const samples = st.samples.filter(s => s.kind === "purposive");
    const sopts = samples.map(s => `<option value="${esc(s.name)}"${s.name === f.sample ? " selected" : ""}>${esc(s.name)} (${esc(s.n)} records, ${s.locked ? "locked" : "not locked"})</option>`).join("")
      || '<option value="">no development sample yet</option>';
    return `<div class="row">
        <label>Development sample <select data-cmp-field="sample">${sopts}</select></label>
        <label>Name <input data-cmp-field="name" size="12" value="${esc(f.name)}" aria-label="Name of the comparison"></label>
        <label>Repeats <input data-cmp-field="repeats" type="number" min="1" max="10" size="3" value="${esc(f.repeats)}" aria-label="Repeats of each configuration"></label>
      </div>
      <div data-cmp-rows>${f.rows.map((r, i) => rowHtml(r, i, f.rows.length, st.providers, st.prompts)).join("")}</div>
      <div class="row"><button type="button" class="secondary" data-cmp-act="add">Add configuration</button></div>
      <p><label><input type="checkbox" data-cmp-field="reuse"${f.reuse ? " checked" : ""}> Reuse identical evaluations already stored (same prompt, record, model, temperature and seed): fewer new calls; each is marked as reused</label></p>
      <div class="row"><button type="button" class="secondary" data-cmp-act="estimate">Estimate</button></div>`;
  }

  // ---------------------------------------------------------------- the gate: estimate, refusal, Run
  function estimateHtml(e) {
    return `<div data-cmp-estimate>${(e.lines || []).map(l => {
      const indent = /^\s/.test(l);
      return `<p${indent ? ' style="margin-left:1.5em"' : ""}>${esc(l.trim())}</p>`;
    }).join("")}</div>`;
  }
  // Run is enabled only for a locked sample whose estimate was made for exactly the choices on the form.
  function gate(st) {
    const s = st.samples.find(x => x.name === st.form.sample);
    const fresh = !!st.estimate && st.estimateKey === JSON.stringify(body(st.form));
    const refusal = s && !s.locked ? notLocked(s.name) : null;
    const disabled = !s || !!refusal || !fresh || st.busy;
    return { s, fresh, refusal, disabled };
  }
  function gateHtml(st) {
    const g = gate(st);
    const parts = [];
    if (g.fresh) parts.push(estimateHtml(st.estimate));
    else if (st.estimate) parts.push("<p>The choices changed since the estimate: estimate again before running.</p>");
    else parts.push("<p>Estimate first: the records, runs, new calls and cost are shown before anything runs.</p>");
    const run = `<button type="button" class="secondary" data-cmp-act="run"${g.disabled ? " disabled" : ""}>Run comparison${g.fresh && !g.refusal ? ` (${esc(st.estimate.new_calls)} new calls)` : ""}</button>`;
    parts.push(`<div class="row">${run}${g.refusal ? ` <span data-cmp-refusal role="status">${esc(g.refusal)}</span>` : ""}</div>`);
    // what the run sends, one sentence per backend chosen above (the server words it, common.js places it)
    for (const b of [...new Set((st.form.rows || []).map(r => r.provider).filter(Boolean))])
      parts.push(`<p class="send-note" data-send="screen" data-send-backend="${esc(b)}" hidden></p>`);
    if (st.message) parts.push(`<p data-cmp-msg role="status"${st.messageBad ? ' class="bad"' : ""}>${esc(st.message)}</p>`);
    return parts.join("");
  }

  // ---------------------------------------------------------------- results
  function consistencyCell(c) {
    const k = c.consistency || {};
    return [k.measured ? esc(k.text) : "", ...(k.notes || []).map(esc)].filter(Boolean).join("<br>") || "–";
  }
  function breakdownHtml(res, key) {
    const rows = [];
    for (const c of res.configs) for (const b of c[key] || []) {
      const label = key === "by_area" && b.terms && b.terms.length ? `${b.key} (${b.terms.slice(0, 5).join(", ")})` : b.key;
      rows.push(`<tr><td>${esc(c.label)}</td><td>${esc(label)}</td><td>${esc(b.n)}</td><td>${esc(b.misses_text)}</td><td>${esc(b.false_includes_text)}</td></tr>`);
    }
    const what = key === "by_area" ? "research area" : "pool";
    return `<h3>${key === "by_area" ? "By research area" : "By pool"}</h3><table data-cmp-${key === "by_area" ? "area" : "pool"}>
      <tr><th>configuration</th><th>${what}</th><th>labelled n</th><th>misses</th><th>false includes</th></tr>${rows.join("")}</table>`;
  }
  function resultsHtml(res, by) {
    if (res.hidden) {   // ui-redesign D4: the figures wait for the development sample; the settings stay
      return `<p data-cmp-hidden role="status">${esc(res.hidden)}</p>
        <p>Comparison ${esc(res.name)} on ${esc(res.sample)}: ${esc(res.configs.length)} configuration${res.configs.length === 1 ? "" : "s"} × ${esc(res.repeats)} repeat${res.repeats === 1 ? "" : "s"}; ${esc(res.labelled_text)}.</p>
        <ul data-cmp-results>${res.configs.map(c => `<li data-cmp-config="${esc(c.label)}">${esc(c.label)}: ${esc(c.provider)} · ${esc(c.model || "?")}, prompt v${esc(c.prompt_version)}, temperature ${esc(c.temperature_text)}</li>`).join("")}</ul>`;
    }
    const out = [`<p>${esc(NOT_CORPUS)}</p>`, ...(res.notes || []).map(n => `<p data-cmp-note>${esc(n)}</p>`),
      `<p>Comparison ${esc(res.name)} on ${esc(res.sample)}: ${esc(res.configs.length)} configuration${res.configs.length === 1 ? "" : "s"} × ${esc(res.repeats)} repeat${res.repeats === 1 ? "" : "s"}; ${esc(res.labelled_text)}. Each figure is the range over the repeats.</p>`];
    out.push(`<table data-cmp-results><tr><th>configuration</th><th>model</th><th>prompt</th><th>temperature</th><th>misses</th><th>false includes</th><th>false maybes</th><th>agree</th><th>scored</th><th>repeats done</th><th>consistency</th><th>tokens in, out</th><th>cost</th><th>failed calls</th></tr>` +
      res.configs.map(c => `<tr data-cmp-config="${esc(c.label)}"><td>${esc(c.label)}</td><td>${esc(c.provider)} · ${esc(c.model || "?")}</td><td>v${esc(c.prompt_version)}</td><td>${esc(c.temperature_text)}</td>
        <td>${esc(c.misses_text)}</td><td>${esc(c.false_includes_text)}</td><td>${esc(c.false_maybes_text)}</td><td>${esc(c.agree_text)}</td><td>${esc(c.scored_text)}</td><td>${esc(c.repeats_done)} of ${esc(res.repeats)}</td>
        <td>${consistencyCell(c)}</td><td>${esc((c.prompt_tokens || 0).toLocaleString())}, ${esc((c.completion_tokens || 0).toLocaleString())}</td><td>${esc(c.cost)}</td><td>${esc(c.failed)}</td></tr>`).join("") + "</table>");
    const changed = res.configs.filter(c => (c.consistency || {}).changed && c.consistency.changed.length);
    if (changed.length) {
      out.push(`<h3>Changed between repeats</h3>` + changed.map(c => `<p><b>${esc(c.label)}</b></p><ul data-cmp-changed="${esc(c.label)}">` +
        c.consistency.changed.map(x => `<li>record ${esc(x.record_id)} ${esc(x.title || "")}: ${esc(x.decisions_text)}</li>`).join("") + "</ul>").join(""));
    }
    if (by === "pool") out.push(breakdownHtml(res, "by_pool"));
    if (by === "area") {
      if (res.areas_message) out.push(`<p data-cmp-areas-msg role="status">${esc(res.areas_message)}</p>`, breakdownHtml(res, "by_pool"));
      else out.push(breakdownHtml(res, "by_area"));
    }
    return out.join("");
  }
  function listHtml(st) {
    if (!st.comparisons.length) return "<p data-cmp-none>No comparison yet.</p>";
    const opts = st.comparisons.map(c => `<option value="${esc(c.name)}"${c.name === st.shownName ? " selected" : ""}>${esc(c.name)} · ${esc(c.sample)} · ${esc(c.configs.join(", "))} · ${esc(c.repeats)} repeats${c.finished_at ? "" : " (not finished)"}</option>`).join("");
    const by = (v, label) => `<option value="${v}"${(st.by || "") === v ? " selected" : ""}>${label}</option>`;
    return `<div class="row"><label>Comparison <select data-cmp-field="shown">${opts}</select></label>
      <label>Break down <select data-cmp-field="by">${by("", "no breakdown")}${by("pool", "By pool")}${by("area", "By research area")}</select></label></div>
      <div data-cmp-shown>${st.shown ? resultsHtml(st.shown, st.by) : st.shownError ? `<p class="bad">${esc(st.shownError)}</p>` : ""}</div>`;
  }

  // ---------------------------------------------------------------- controller
  // parts: {form, gate, results} elements; deps: {api, startJob(path, opts, onDone)}. Nothing is sent but the GETs
  // of load() and show(), the estimate POST and, on Run, the job POST.
  function controller(parts, deps) {
    const st = { samples: [], providers: [], prompts: [], comparisons: [], form: null, estimate: null, estimateKey: null,
                 busy: false, message: "", messageBad: false, shownName: null, shown: null, shownError: null, by: "", nameEdited: false };
    const drawForm = () => { parts.form.innerHTML = formHtml(st); };
    const drawGate = () => { parts.gate.innerHTML = gateHtml(st); };
    const drawResults = () => { parts.results.innerHTML = listHtml(st); };
    const say = (msg, bad = false) => { st.message = msg; st.messageBad = bad; drawGate(); };
    async function load({ prompts = [] } = {}) {
      const [samples, prov, list] = await Promise.all([deps.api("/api/samples"), deps.api("/api/providers"), deps.api("/api/screen/compare")]);
      st.samples = Object.entries(samples.samples || {}).map(([name, s]) => ({ name, kind: s.kind, n: s.n, locked: !!s.locked }));
      st.providers = prov.providers || []; st.prompts = prompts; st.comparisons = list.comparisons || [];
      const has = (n) => n && st.providers.some(r => r.name === n);
      const first = [(prov.stage_providers || {}).screen, prov.default].find(has) || (st.providers[0] || {}).name || "";
      const purposive = st.samples.filter(s => s.kind === "purposive").map(s => s.name);
      if (!st.form) st.form = { name: nextName(st.comparisons), sample: purposive.includes("development") ? "development" : (purposive[0] || ""),
                                rows: [{ provider: first, prompt: "", temperature: "" }], repeats: 3, reuse: false };
      else {
        if (!purposive.includes(st.form.sample)) st.form.sample = purposive[0] || "";
        for (const r of st.form.rows) if (!has(r.provider)) r.provider = first;
        if (!st.nameEdited) st.form.name = nextName(st.comparisons);
      }
      if (!st.shownName || !st.comparisons.some(c => c.name === st.shownName)) st.shownName = st.comparisons.length ? st.comparisons[st.comparisons.length - 1].name : null;
      drawForm(); drawGate();
      await show(st.shownName);
    }
    async function show(name) {
      st.shownName = name; st.shown = null; st.shownError = null;
      if (name) {
        try { st.shown = await deps.api(`/api/screen/compare/${encodeURIComponent(name)}`); }
        catch (e) { st.shownError = e.message; }
      }
      drawResults();
    }
    // a form field changed: the estimate no longer matches unless the choices are the same again
    function set(field, value) {
      if (field === "shown") return show(value);
      if (field === "by") { st.by = value; drawResults(); return; }
      if (field === "name") st.nameEdited = true;
      st.form[field] = field === "reuse" ? !!value : value;
      st.message = ""; drawGate();
    }
    function setRow(i, field, value) { st.form.rows[i][field] = value; st.message = ""; drawGate(); }
    function addRow() { const last = st.form.rows[st.form.rows.length - 1] || {}; st.form.rows.push({ provider: last.provider || "", prompt: "", temperature: "" }); drawForm(); drawGate(); }
    function removeRow(i) { if (st.form.rows.length > 1) st.form.rows.splice(i, 1); drawForm(); drawGate(); }
    async function estimate() {
      const b = body(st.form);
      st.message = "";
      try {
        st.estimate = await deps.api("/api/screen/compare/estimate", { method: "POST", body: JSON.stringify(b) });
        st.estimateKey = JSON.stringify(b); drawGate();
      } catch (e) { st.estimate = null; st.estimateKey = null; say(e.message, true); }
    }
    async function run() {
      if (gate(st).disabled) return;
      const b = body(st.form);
      st.busy = true; say("");
      try {
        const r = await deps.startJob("/api/jobs/compare", { body: JSON.stringify(b) }, async () => { st.shownName = b.name; st.message = ""; await load({ prompts: st.prompts }); });
        st.estimate = null; st.estimateKey = null;
        st.message = r && r.job_id ? "Running in the background: progress, time left and cost are in the bar at the top. You can leave this page." : (r && r.message) || "";
        st.messageBad = false;
      } catch (e) { st.message = e.message; st.messageBad = true; }
      finally { st.busy = false; drawGate(); }
    }
    return { load, show, set, setRow, addRow, removeRow, estimate, run, state: st, gate: () => gate(st) };
  }

  return { NOT_LOCKED, NOT_CORPUS, notLocked, configText, body, nextName, formHtml, gateHtml, estimateHtml, resultsHtml, listHtml, controller };
})();
if (typeof module !== "undefined" && module.exports) module.exports = Compare;
