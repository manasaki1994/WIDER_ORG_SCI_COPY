// Model for query creation (#model, the first page of a project, not a stage): the LLM that writes the concept,
// synonym and search-string proposals. While no construct backend is set the page shows three guided steps (where it
// runs, which model, check it); after that, one card per backend (pickers.js) with the chosen card marked. Choosing a
// card in the grid, or "Use ... for query creation" in step 3, saves it as the construct backend and then as the
// project default: two History entries, as `wider providers stage construct NAME` and `wider providers set-default
// NAME`. Opening the page reads the backends only: the detection (127.0.0.1 only), the preset list, the model list and
// Check run when pressed, and Check on a backend off this computer asks for consent first (api(), common.js). Edit,
// Remove, Revoke and the backend per stage are on Settings, which opens the same backend form (mountForm).
const ModelPage = (() => {
  const q = (s) => document.querySelector(s);
  // every write tells app.js that project.yaml changed, so the sidebar and the stage marks refresh
  const changed = (r) => { window.dispatchEvent(new CustomEvent("wider:changed")); return r; };
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) }).then(changed);
  const opt = (values) => values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
  const flash = (el, msg, bad) => { if (!el) return; el.innerHTML = bad ? `<span class="bad">${esc(msg)}</span>` : esc(msg); };
  const wrap = (fn, msgEl) => async (...a) => { try { await fn(...a); } catch (e) { flash(typeof msgEl === "function" ? msgEl() : msgEl, e.message, true); } };
  const numOrNull = (v) => (String(v).trim() === "" ? null : Number(v));

  let presets = null;   // GET /api/providers/presets (the catalogue, no request off this computer), loaded when first needed
  const loadPresets = async () => presets || (presets = (await api("/api/providers/presets").catch(() => ({ presets: [] }))).presets);
  const presetOf = (id) => (presets || []).find(x => x.id === id) || null;

  // ------------------------------------------------------------------ the backend form (this page and Settings)
  // `pre` prefixes every id, so the model page's form and Settings' form can both be in the page (inspection 1 Nov,
  // item 18: duplicate ids); the model page's has none, Settings' "st-"
  function formHtml(kinds, listId, pre = "") {
    return formBody(kinds, listId).replace(/ id="(?=backend-)/g, ` id="${pre}`);
  }
  function formBody(kinds, listId) {
    return `<h3 id="backend-form-title" tabindex="-1">Add AI model</h3>
      <form id="backend-form" class="form-grid">
        <label>Name <input name="name" required placeholder="e.g. local-llama"></label>
        <label>Kind <select name="kind">${opt(kinds)}</select></label>
        <p class="warn-text" id="backend-exp-warning" hidden></p>
        <label id="backend-exp-ack" hidden><input type="checkbox" name="accept_experimental">
          I understand this model setup is experimental and add it anyway</label>
        <p id="backend-signin" hidden></p>
        <div><label>Model <input name="model" list="${listId}" placeholder="model name as the service names it (free text)"></label>
          <datalist id="${listId}"></datalist>
          <span class="row"><button type="button" class="secondary small-btn" id="backend-load-models">Load models</button>
          <span class="muted small" id="backend-models-msg" role="status"></span></span><span class="send-note" data-send="models" hidden></span></div>
        <label data-for="openai_compat ollama anthropic litellm">Base URL <input name="base_url" placeholder="http://127.0.0.1:11434 or https://api.example.com/v1"></label>
        <label data-for="openai_compat anthropic litellm">API key environment variable (name only)
          <input name="api_key_env" placeholder="OPENAI_API_KEY"><span class="muted small">Set the variable in the shell that starts WIDER; its value is read from the environment and never written to the project.</span></label>
        <label data-for="cli_agent">Command (JSON argv list; {model}, {system_file}, {system_toml}, {schema_file}, {system} and {user} are replaced; the user message goes to standard input)
          <input name="command" placeholder='["claude", "-p", "--model", "{model}", "--system-prompt-file", "{system_file}", "--output-format", "json"]'></label>
        <label data-for="openai_compat anthropic litellm cli_agent">Prices in US dollars per million tokens (optional, for the cost display)
          <span class="row"><input name="price_input_per_mtok" type="number" min="0" step="any" placeholder="input" aria-label="Price per million input tokens" style="width:120px">
          <input name="price_output_per_mtok" type="number" min="0" step="any" placeholder="output" aria-label="Price per million output tokens" style="width:120px"></span>
          <span class="muted small">From the vendor's price list. Models on this computer cost nothing; an agentic CLI that reports its own cost needs none.</span></label>
        <label class="inline"><input type="checkbox" name="set_default"> make default</label>
        <details id="backend-advanced"><summary>Advanced</summary>
          <div class="form-grid">
            <label>Cached input price per million tokens <input name="price_cached_input_per_mtok" type="number" min="0" step="any"></label>
            <label>Cache write price per million tokens <input name="price_cache_write_per_mtok" type="number" min="0" step="any"></label>
            <label>Maximum output tokens (max_output_tokens) <input name="max_output_tokens" type="number" min="1" step="1"></label>
            <label>Temperature <input name="temperature" type="number" min="0" step="any"></label>
            <label>Timeout in seconds <input name="timeout_s" type="number" min="1" step="1"></label>
            <label>Parallel requests <input name="max_concurrency" type="number" min="1" step="1"></label>
            <fieldset><legend>Extra request headers</legend><div id="backend-headers"></div>
              <button type="button" class="secondary small-btn" id="backend-add-header">Add header</button></fieldset>
            <label>Other settings (key=value per line)
              <textarea name="extra" rows="3" placeholder="auth=bearer"></textarea></label>
          </div></details>
        <div class="row"><button type="submit" class="secondary">Save model</button> <button type="button" class="secondary" id="backend-cancel">Cancel</button></div>
        <span id="backend-form-msg" role="status"></span>
      </form>`;
  }

  // Draw the backend form into `formBox` (hidden until opened) and return open(values), with the values of
  // backends.js (formValuesFromPreset / formValuesFromBackend). "Add" or "Edit" opens it titled "Add backend" or
  // "Edit <name>"; Cancel saves nothing. Adding over an existing name asks first (Replace, Edit instead, Cancel).
  // `d` is GET /api/providers; onSaved(name) runs after a save, onCancel() after Cancel. Every lookup stays inside
  // `formBox`, so the model page and Settings each hold their own form; `listId` names the model datalist.
  function mountForm(formBox, d, { onSaved, onCancel, listId = "backend-model-list", idPrefix = "" } = {}) {
    const q = (s) => formBox.querySelector(s.replace(/^#backend-/, `#${idPrefix}backend-`));
    formBox.innerHTML = formHtml(d.kinds || [], listId, idPrefix);
    formBox.hidden = true;
    const fmsg = q("#backend-form-msg"), form = q("#backend-form");
    let state = null;   // the form values it was opened with (mode, preset, extra before the edit)
    const showFields = () => { const k = form.kind.value; form.querySelectorAll("[data-for]").forEach(l => l.hidden = !l.dataset.for.split(" ").includes(k)); };
    form.kind.onchange = showFields;
    const headerRows = () => [...q("#backend-headers").querySelectorAll("[data-header-row]")];
    const addHeader = (name, value) => {
      const i = headerRows().length;
      q("#backend-headers").insertAdjacentHTML("beforeend", Backends.headerFieldHtml(name, value, i));
      bindHeaders();
    };
    // a header whose name turns sensitive switches its value field to a variable name (the typed value is dropped)
    const bindHeaders = () => headerRows().forEach((row, i) => {
      const nameEl = row.querySelector("[data-header-name]");
      nameEl.onchange = () => { const v = row.querySelector("[data-header-value]").value; row.outerHTML = Backends.headerFieldHtml(nameEl.value, Backends.isSensitiveHeader(nameEl.value) ? "" : v, i); bindHeaders(); };
    });
    q("#backend-add-header").onclick = () => { addHeader("", ""); const r = headerRows().pop(); r.querySelector("input").focus(); };
    const loadModels = wrap(async () => {
      const mmsg = q("#backend-models-msg"); mmsg.textContent = "loading…";
      const r = state.mode === "edit" ? await api(`/api/providers/${encodeURIComponent(state.name)}/models`)
        : await post("/api/providers/models", { ...(state.preset ? { preset: state.preset } : { kind: form.kind.value }), base_url: form.base_url.value.trim() || null });
      const list = (r.models || []).map(m => (typeof m === "string" ? m : m.id || m.name)).filter(Boolean);
      q(`#${listId}`).innerHTML = [...new Set([...Backends.modelOptions(state, presetOf(state.preset)).suggested, ...list])].map(m => `<option value="${esc(m)}">`).join("");
      mmsg.textContent = list.length ? `${list.length} models loaded` : "no models listed; type the model id";
    }, () => q("#backend-models-msg"));
    const open = (v) => {
      state = v; form.reset(); fmsg.textContent = "";
      q("#backend-form-title").textContent = v.title;
      form.name.value = v.name; form.name.readOnly = v.mode === "edit";
      form.kind.value = v.kind || "openai_compat"; form.kind.disabled = v.mode === "edit";
      for (const k of ["model", "base_url", "api_key_env", "command", "timeout_s", "max_concurrency", "temperature",
        "price_input_per_mtok", "price_output_per_mtok", "price_cached_input_per_mtok", "price_cache_write_per_mtok"]) form[k].value = v[k] ?? "";
      form.max_output_tokens.value = (v.extra || {}).max_output_tokens ?? "";
      const rest = { ...(v.extra || {}) }; delete rest.max_output_tokens;
      form.extra.value = Backends.extraToLines(rest);
      // every setting in view: a line hidden below the box's edge cannot be seen, so it cannot be removed either
      form.extra.rows = Math.max(3, form.extra.value.split("\n").length + 1);
      q("#backend-headers").innerHTML = ""; Object.entries((v.extra || {}).headers || {}).forEach(([h, val]) => addHeader(h, val));
      q("#backend-exp-warning").hidden = q("#backend-exp-ack").hidden = !v.experimental;
      q("#backend-exp-warning").textContent = v.warning || "";
      q("#backend-signin").hidden = !v.signin; q("#backend-signin").textContent = v.signin || "";
      const mo = Backends.modelOptions(v, presetOf(v.preset));
      q(`#${listId}`).innerHTML = mo.suggested.map(m => `<option value="${esc(m)}">`).join("");
      q("#backend-load-models").textContent = mo.loadLabel; q("#backend-load-models").onclick = loadModels;
      q("#backend-models-msg").textContent = "";
      showFields(); formBox.hidden = false; q("#backend-form-title").focus();
      if (mo.autoLoad) loadModels();   // a loopback server or a CLI catalogue only; a cloud list waits for the button
    };
    // the form as a request body; on an edit, a removed extra key is sent empty so the server drops it
    const formBody = () => {
      const old = state.mode === "edit" ? (state.extra || {}) : {};
      const extra = Backends.linesToExtra(form.extra.value, Object.fromEntries(Object.entries(old).filter(([k]) => k !== "max_output_tokens")));
      if (form.max_output_tokens.value !== "") extra.max_output_tokens = Number(form.max_output_tokens.value);
      else if ("max_output_tokens" in old) extra.max_output_tokens = "";
      const headers = {};
      for (const row of headerRows()) {
        const h = row.querySelector("[data-header-name]").value.trim(); if (!h) continue;
        headers[h] = Backends.headerValue(h, row.querySelector("[data-header-value]").value);
      }
      if (Object.keys(headers).length) extra.headers = headers; else if ("headers" in old) extra.headers = "";
      const b = { model: form.model.value.trim(), base_url: form.base_url.value.trim() || null,
        api_key_env: form.api_key_env.value.trim() || null, command: form.command.value.trim() || null, extra };
      for (const k of ["timeout_s", "max_concurrency", "temperature", "price_input_per_mtok", "price_output_per_mtok",
        "price_cached_input_per_mtok", "price_cache_write_per_mtok"]) b[k] = numOrNull(form[k].value);
      return b;
    };
    form.onsubmit = wrap(async (ev) => {
      ev.preventDefault();
      const b = formBody(), name = form.name.value.trim();
      // a CLI or cloud preset needs a model; the API's refusal names a CLI command, while here the field and its button
      // are right there
      const fam = (presetOf(state.preset) || {}).family;
      if (!b.model && state.mode !== "edit" && (fam === "cli" || fam === "cloud"))
        throw new Error("Type a model id, or press the button under Model to load the list.");
      if (state.mode === "edit") {
        await api(`/api/providers/${encodeURIComponent(state.name)}`, { method: "PATCH", body: JSON.stringify(b) }).then(changed);
        if (form.set_default.checked) await post("/api/providers/default", { name: state.name });
      } else {
        const add = { ...b, name, kind: form.kind.value, preset: state.preset || null, set_default: form.set_default.checked,
          accept_experimental: form.accept_experimental.checked };
        try { await post("/api/providers", add); } catch (e) {
          if (e.status !== 409 || !e.detail || e.detail.error !== "provider_exists") throw e;
          const c = await askChoice(Backends.replaceDialogHtml(e.detail));
          if (!c) return;   // Cancel: nothing saved, the form stays as typed
          if (c.act === "edit") { open(Backends.formValuesFromBackend(d.providers.find(p => p.name === name))); return; }
          await post("/api/providers", { ...add, replace: true });
        }
      }
      formBox.hidden = true; state = null;
      if (onSaved) await onSaved(name);
    }, fmsg);
    q("#backend-cancel").onclick = () => { form.reset(); formBox.hidden = true; state = null; if (onCancel) onCancel(); };
    return open;
  }

  // ------------------------------------------------------------------ the page
  const WHERE = [
    ["cli", "CLI", "An agentic command-line tool you already use (such as Claude Code, Codex or Gemini CLI). It sends the text to its vendor."],
    ["cloud", "Cloud", "An API such as OpenAI or Anthropic. It needs a key variable; the text leaves this computer."],
    ["local", "Local", "A model server on this computer (Ollama, LM Studio, llama.cpp, vLLM). Nothing leaves this computer."],
  ];
  // the step 1 family of a saved backend: an agentic CLI, a server on this computer or the local network, or an API
  const familyOf = (p) => (p.kind === "cli_agent" ? "cli"
    : (p.locality === "local" || p.locality === "network" || (p.kind === "ollama" && p.locality !== "cloud")) ? "local" : "cloud");
  let where = null;     // step 1: "cli", "cloud" or "local"
  let picked = null;    // step 2: the backend to check and use (nothing is saved until "Use")
  let detected = null;  // GET /api/backends/detect rows, once "Find" was pressed on this page
  let checked = null;   // {name, html}: the last Check result, kept across redraws

  // `withError` false: the card's own last check line already shows the error, so it is not repeated under it
  const probeText = (r, withError = true) => (r.ok ? '<span class="ok">ok</span>' : '<span class="bad">failed</span>') +
    ` ${esc(r.elapsed_s)}s, ${esc(r.attempts)} attempt${r.attempts === 1 ? "" : "s"}` + (withError && r.error ? ` · ${esc(r.error)}` : "") +
    (r.parsed ? `<div class="muted">parsed: <code>${esc(JSON.stringify(r.parsed))}</code></div>` : "");

  // Find is the page's one filled button only while no model is picked or set; then "Use ..." or the can-do box's
  // "Continue to ..." is, and Find is an outline button (D32 F15)
  const findHtml = (main) => `<div class="row"><button type="button"${main ? "" : ' class="secondary"'} id="backend-find">Find AI models on this computer</button>
      <button type="button" class="secondary" id="backend-add-hand">Add an AI model by hand</button></div>
    <p class="muted small">Find looks for installed command-line tools, key variables and model servers on this computer
      (127.0.0.1 only); nothing is sent off this computer.</p>
    <div id="backend-cards" aria-live="polite"></div>
    <div id="backend-form-box" class="card-inset" hidden></div>`;

  function guidedHtml(d) {
    const mine = d.providers.filter(p => !where || familyOf(p) === where);
    const pick = d.providers.find(p => p.name === picked);
    return `<p class="muted">This model writes the proposals for the concepts, synonyms and search strings. Three steps:
        where it runs, which model, and a check that it answers. Nothing is sent anywhere until you press Check.</p>
      <ol class="interview model-steps">
      <li class="step" data-mstep="where"><div class="step-head"><span class="step-num">1</span><h2>Where should the model run?</h2></div>
        <div class="where-choices" role="group" aria-label="Where the model runs">
        ${WHERE.map(([k, label, why]) => `<p><button type="button" class="secondary small-btn" data-where="${k}"
          aria-pressed="${where === k}">${label}</button> <span class="small">${why}</span></p>`).join("")}</div>
        ${findHtml(!pick)}</li>
      <li class="step" data-mstep="which"><div class="step-head"><span class="step-num">2</span><h2>Which model?</h2></div>
        ${mine.length ? Pickers.cardsHtml(mine, { kind: "llm", chosen: picked })
          : `<p class="muted" data-no-cards>No ${where ? `${WHERE.find(w => w[0] === where)[1]} ` : ""}AI model is set up yet: press
            "Find AI models on this computer" or "Add an AI model by hand" in step 1.</p>`}</li>
      <li class="step" data-mstep="check"><div class="step-head"><span class="step-num">3</span><h2>Check it</h2></div>
        ${pick ? `<p><b>${esc(pick.name)}</b> <span class="muted">(${esc(pick.model)})</span> ${Pickers.chipHtml(pick)}
            <span class="small">sends to ${esc(pick.destination || "?")}</span></p>
          <p class="muted small">Check sends one fixed test question and shows the answer. A model off this computer asks
            for your consent first.</p>
          <div class="row"><button type="button" class="secondary" data-pact="check" data-name="${esc(pick.name)}">Check ${esc(pick.name)}</button>
            <button type="button" id="model-use" data-name="${esc(pick.name)}">Use ${esc(pick.name)} for query creation</button></div>
          <p class="send-note" data-send="backend_check" data-send-backend="${esc(pick.name)}" hidden></p>
          <p class="small" id="model-check-result" role="status"></p>`
        : `<p class="muted">Choose a model in step 2 first.</p>${d.providers.some(p => p.name === d.default)
          ? `<p>Until you choose, the proposals use the project default, <b>${esc(d.default)}</b>.</p>` : ""}`}</li></ol>`;
  }

  function gridHtml(d, construct) {
    return `<p class="muted">The model that writes the proposals for the concepts, synonyms and search strings is
        <b>${esc(construct)}</b>. Choosing another card makes it the model for query creation and the project default.
        Edit, remove, consent and the model per stage are on <a href="#settings">Settings</a>.</p>
      ${Pickers.cardsHtml(d.providers, { kind: "llm", chosen: construct, actions: ["check"] })}
      ${findHtml(false)}`;
  }

  async function loadModel() {
    const box = q("#model-body"); if (!box) return;
    const d = await api("/api/providers");
    const construct = (d.stage_providers || {}).construct || null;
    if (construct) { where = null; picked = null; }
    const pick = d.providers.find(p => p.name === picked);
    if (!pick) picked = null;
    else if (where && familyOf(pick) !== where) where = familyOf(pick);   // a backend just added under another family
    box.innerHTML = `${construct ? gridHtml(d, construct) : guidedHtml(d)}
      <span class="muted small" id="model-msg" role="status"></span>`;
    const msg = () => q("#model-msg");
    const openForm = mountForm(q("#backend-form-box"), d, {
      onSaved: async (name) => { if (!construct) picked = name; await loadModel(); flash(msg(), `saved ${name}`); },
      onCancel: () => q("#backend-add-hand").focus() });
    const showCards = () => {
      const cards = q("#backend-cards");
      cards.innerHTML = Backends.renderPresetCards((detected || []).filter(r => !where || r.family === where), d.providers);
      cards.querySelectorAll("[data-preset-add]").forEach(b => b.onclick = () => {
        const p = presetOf(b.dataset.presetAdd);
        if (p) openForm(Backends.formValuesFromPreset(p, d.providers));
      });
      cards.querySelectorAll("[data-edit-backend]").forEach(b => b.onclick = () =>
        openForm(Backends.formValuesFromBackend(d.providers.find(p => p.name === b.dataset.editBackend))));
    };
    if (detected) showCards();
    q("#backend-find").onclick = wrap(async () => {
      const cards = q("#backend-cards"), btn = q("#backend-find");
      btn.disabled = true; cards.textContent = "looking…";
      try { await loadPresets(); detected = (await api("/api/backends/detect")).rows; showCards(); }
      finally { btn.disabled = false; }
    }, msg);
    q("#backend-add-hand").onclick = wrap(async () => {
      await loadPresets();
      const kind = { cli: "cli_agent", local: "ollama" }[where] || "openai_compat";
      openForm({ ...Backends.formValuesFromPreset({ id: "backend", kind }, d.providers), preset: null, name: "" });
    }, msg);
    box.querySelectorAll("[data-where]").forEach(b => b.onclick = () => {
      where = b.dataset.where;
      if (picked && familyOf(d.providers.find(p => p.name === picked)) !== where) picked = null;
      loadModel().then(() => { const w = q(`[data-where="${where}"]`); if (w) w.focus(); }).catch(e => flash(msg(), e.message, true));
    });
    // choosing: in the steps it only marks the card for step 3; in the grid it saves at once
    const use = wrap(async (name) => {
      await post("/api/providers/stage", { stage: "construct", name });
      await post("/api/providers/default", { name });
      picked = null; where = null;
      if (window.afterAction) await afterAction(); else await loadModel();
      flash(msg(), `${name} is now the model for query creation and the project default.`);
    }, msg);
    Pickers.wire(box, (name) => {
      if (construct) return use(name);
      picked = name; checked = checked && checked.name === name ? checked : null;
      return loadModel();
    }, wrap(async (act, name, b) => {
      if (act !== "check") return;
      const cell = construct ? box.querySelector(`[data-presult="${CSS.escape(name)}"]`) : q("#model-check-result");
      b.disabled = true; cell.textContent = "checking…";
      let html;
      try { html = probeText(await api(`/api/providers/${encodeURIComponent(name)}/probe`, { method: "POST" }), !construct); }
      catch (e) { html = `<span class="bad">${esc(e.message)}</span>`; }
      checked = { name, html };
      await loadModel();   // the card's last check line is redrawn from the server
    }, msg));
    const useBtn = q("#model-use");
    if (useBtn) useBtn.onclick = () => use(picked);
    if (checked) {
      const cell = construct ? box.querySelector(`[data-presult="${CSS.escape(checked.name)}"]`) : (picked === checked.name ? q("#model-check-result") : null);
      if (cell) cell.innerHTML = checked.html;
    }
  }

  return { load: loadModel, mountForm, familyOf };
})();
window.loadModel = ModelPage.load;
