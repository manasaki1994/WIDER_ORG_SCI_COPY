// Text builders for the backend dialogs and cards: the preset cards of "Find AI models on this computer", the values of
// the backend and embedding forms, the model dropdown, the replace confirmation, cost ranges, the backend-check text and
// the consent card (Setup and Settings); the backend-change dialog, the new-result-set dialog, the "Models used" card,
// the "Result sets" card and the set comparison (LLM screening and Recall pages). They build HTML strings and plain
// values only (no DOM, no requests), so tests run them under Node; every value goes through `esc` (common.js).
const Backends = (() => {
  const n = (x) => (x == null ? "" : Number(x).toLocaleString());
  const usd = (x) => `$${Number(x || 0).toFixed(x >= 1 ? 2 : 4)}`;

  const MIXED = '<span class="chip mixed" title="evaluations by more than one model">mixed</span>';
  // the visible name of the test model (UX review F11): never a real review
  const TEST_MODEL = "Test model (not for real reviews)";
  const mixedBadge = (on) => (on ? ` ${MIXED}` : "");
  const expBadge = (note) => (note ? ` <span class="chip warn">${esc(note)}</span>` : "");
  const side = (s) => `${esc((s || {}).provider || "?")} ${esc((s || {}).model || "")}`.trim();

  // 409 backend_change: {run_id, case, done, total, from, to, changed}. "Start a separate run" only in case (b).
  function backendChangeHtml(c) {
    return `<h2 id="choice-title">The model changed since this run started</h2>
      <p>Run <code>${esc(c.run_id)}</code>: ${n(c.done)} of ${n(c.total)} records done.</p>
      <table class="summary-table"><tr><td>from</td><td>${side(c.from)}</td></tr><tr><td>to</td><td>${side(c.to)}</td></tr>
      <tr><td>changed</td><td>${(c.changed || []).map(x => esc(x)).join(", ") || "nothing"}</td></tr></table>
      <p class="muted small">Continuing records the change in the run folder and marks the run as mixed. Nothing has run yet.</p>
      <div class="row"><button type="button" data-act="continue">Continue this run on the new model</button>
      ${c.case === "b" ? '<button type="button" class="secondary" data-act="new">Start a separate run</button>' : ""}
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }

  // 409 new_set_needed: {protocol, set, changed, reusable: {set, count}|null, to_screen, in_scope, same_as, rule,
  // estimate: {all, reuse}}, with the refusal text in `detail`.
  // The refusal text names the CLI flags (--new-set, --reuse-from); the dialog's buttons are their UI twins, so
  // those hints are dropped here.
  const dropCliHints = (t) => String(t || "").replace(/\s*\([^)]*--new-set[^)]*\)/g, "").replace(/\s*[^.;]*--new-set[^.;]*[.;]?/g, "").trim();
  function newSetHtml(r, detail) {
    const rus = r.reusable, est = r.estimate || {};
    const range = (e) => (e && e.priced ? `${usd(e.min_usd)}–${usd(e.max_usd)} for ${n(e.calls)} calls` : (e ? `${n(e.calls)} calls, not priced` : "not estimated"));
    return `<h2 id="choice-title">Screen again as a new result set</h2>
      <p>${esc(dropCliHints(detail))}</p>
      <table class="summary-table"><tr><td>new set</td><td>${esc(r.protocol)} set ${esc(r.set)}</td></tr>
      <tr><td>records in scope</td><td>${n(r.in_scope)}</td></tr>
      ${(r.changed || []).length ? `<tr><td>changed</td><td>${r.changed.map(x => esc(x)).join(", ")}</td></tr>` : ""}
      ${rus ? `<tr><td>reused</td><td>${n(rus.count)} decisions from set ${esc(rus.set)}; ${n(r.to_screen)} records to screen (${esc(range(est.reuse))})</td></tr>` : ""}
      <tr><td>screen everything</td><td>${n(r.in_scope)} records (${esc(range(est.all))})</td></tr>
      ${r.same_as ? `<tr><td>note</td><td>set ${esc(r.same_as)} already holds these settings</td></tr>` : ""}</table>
      <label class="inline">Label of the new set <input data-field="set_label" maxlength="120"></label>
      <p class="muted small">The active set stays as it is until you choose "Use for analysis" in Result sets.</p>
      <div class="row">${rus ? `<button type="button" data-act="reuse">Reuse ${n(rus.count)} decisions from set ${esc(rus.set)}</button>` : ""}
      <button type="button" class="${rus ? "secondary" : ""}" data-act="all">Screen all ${n(r.in_scope)} records</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }

  // GET /api/screen/models: per model rows and totals.
  function modelsCardHtml(s) {
    const sc = s.scope || {};
    const where = sc.run_id ? `run <code>${esc(sc.run_id)}</code>` : `${esc(sc.protocol)} set ${esc(sc.result_set)}${sc.label ? " · " + esc(sc.label) : ""}`;
    const row = (m) => `<tr><td>${esc(m.model)}</td><td>${esc((m.passes || []).join(", "))}</td><td>${n(m.records)}</td><td>${n(m.evaluations)}${m.reused ? ` (${n(m.reused)} reused)` : ""}</td>
      <td>${n(m.calls)}</td><td>${n(m.include)}</td><td>${n(m.maybe)}</td><td>${n(m.exclude)}</td>
      <td>${n(m.prompt_tokens)} / ${n(m.completion_tokens)}</td><td>${esc(m.cost)}</td><td>${n(m.final_records)}</td></tr>`;
    const t = s.totals || {};
    return `<p><b>${esc(s.headline)}</b> · ${where}${mixedBadge(s.mixed)}${expBadge(s.note)}</p>
      ${sc.role_note ? `<p class="muted small">${esc(sc.role_note)}</p>` : ""}
      <table class="check-table"><tr><th>model</th><th>passes</th><th>records</th><th>evaluations</th><th>calls</th><th>include</th><th>maybe</th><th>exclude</th><th>tokens in / out</th><th>cost</th><th>final decisions</th></tr>
      ${(s.models || []).map(row).join("")}
      <tr><td><b>total</b></td><td></td><td>${n(t.records)}</td><td>${n(t.evaluations)}</td><td>${n(t.calls)}</td><td>${n(t.include)}</td><td>${n(t.maybe)}</td><td>${n(t.exclude)}</td>
      <td>${n(t.prompt_tokens)} / ${n(t.completion_tokens)}</td><td>${esc(t.cost)}</td><td></td></tr></table>
      ${s.hidden ? `<p role="note" data-models-hidden>${esc(s.hidden)}.</p>` : ""}
      ${Object.keys(s.origins || {}).length ? `<p class="muted small">Decisions by origin: ${Object.entries(s.origins).map(([k, v]) => `${esc(k)} ${n(v)}`).join(", ")}</p>` : ""}`;
  }

  // GET /api/screen/sets: one row per set with its actions (data-set-act, data-n).
  function setsCardHtml(protocol, sets) {
    if (!(sets || []).length) return `<p class="muted small">${esc(protocol)} has no result set yet.</p>`;
    const when = (t) => esc(fmtWhen(t));
    const row = (s) => `<tr${s.active ? ' class="active-set"' : ""}><td>${esc(s.result_set)}${s.active ? ' <span class="chip">active</span>' : ""}</td>
      <td>${esc(s.label || "")}</td><td>${esc(s.source || "")}${s.source_protocol ? ` from ${esc(s.source_protocol)} set ${esc(s.source_set)}` : ""}</td>
      <td>${esc(s.scope || "")}</td><td>${esc(s.role || "")}${s.role_note ? ` <span class="muted small">(${esc(s.role_note)})</span>` : ""}${mixedBadge(s.mixed)}</td>
      <td>${(s.models || []).map(x => esc(x)).join(", ")}</td><td class="small">${(s.run_ids || []).map(x => esc(x)).join("<br>")}</td><td>${when(s.last_decided_at)}</td>
      <td>${n(s.n)}: ${n(s.include)} / ${n(s.maybe)} / ${n(s.exclude)}${s.hidden ? `<p role="note">${esc(s.hidden)}; the counts leave them out.</p>` : ""}</td>
      <td>${s.active ? "" : `<button type="button" class="secondary small-btn" data-set-act="use" data-n="${esc(s.result_set)}">Use for analysis</button>`}
      <button type="button" class="secondary small-btn" data-set-act="label" data-n="${esc(s.result_set)}">Rename</button></td></tr>`;
    return `<table class="check-table"><tr><th>set</th><th>label</th><th>source</th><th>scope</th><th>role</th><th>models</th><th>runs</th><th>last decision</th><th>n: include / maybe / exclude</th><th></th></tr>
      ${sets.map(row).join("")}</table>`;
  }

  // "Use for analysis" refused because the set was built from another set that is not active: offer "Use both".
  function useRefusalHtml(message) {
    return `<h2 id="choice-title">Use this result set for analysis?</h2><p>${esc(message)}</p>
      <div class="row"><button type="button" data-act="both">Use both</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }

  // GET /api/screen/sets/{protocol}/compare: a 3x3 table, the agreement and the records that differ.
  function compareHtml(c) {
    const o = ["include", "maybe", "exclude"];
    const pct = c.agreement == null ? "-" : `${(c.agreement * 100).toFixed(1)}%`;
    return `<p>${esc(c.protocol)} set ${esc(c.a)} against set ${esc(c.b)}${c.models_only ? " (models only)" : ""}: ${n(c.agree)} of ${n(c.n)} records agree (${pct}).
      ${c.only_a || c.only_b ? `Only in set ${esc(c.a)}: ${n(c.only_a)}; only in set ${esc(c.b)}: ${n(c.only_b)}.` : ""}</p>
      <table class="check-table"><tr><th>set ${esc(c.a)} \\ set ${esc(c.b)}</th>${o.map(x => `<th>${x}</th>`).join("")}</tr>
      ${o.map(x => `<tr><td>${x}</td>${o.map(y => `<td>${n((c.table[x] || {})[y])}</td>`).join("")}</tr>`).join("")}</table>
      ${c.hidden ? `<p role="note" data-compare-hidden>${esc(c.hidden)}.</p>` : ""}
      ${(c.differ || []).length ? `<table class="check-table"><tr><th>record</th><th>title</th><th>set ${esc(c.a)}</th><th>set ${esc(c.b)}</th></tr>
      ${c.differ.map(d => `<tr><td>${esc(d.record_id)}</td><td>${esc(d.title)}</td><td>${esc(d.a)}</td><td>${esc(d.b)}</td></tr>`).join("")}</table>` : ""}`;
  }


  // ---------------------------------------------------------------- backend setup (Setup models step, Settings)
  const FAMILIES = [["cli", "CLI"], ["cloud", "Cloud"], ["local", "Local"]];
  const LOOPBACK = ["127.0.0.1", "localhost", "::1"];
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^\[|\]$/g, ""); } catch (e) { return ""; } };
  const isLoopback = (url) => LOOPBACK.includes(hostOf(url));
  const freeName = (base, names) => { if (!names.includes(base)) return base; let i = 2; while (names.includes(`${base}-${i}`)) i++; return `${base}-${i}`; };
  const localityChip = (loc) => (loc === "local" ? '<span class="chip loc-local">local</span>' : '<span class="chip loc-cloud">remote</span>');
  const EXPERIMENTAL = '<span class="chip warn">Experimental</span>';
  // the saved backends made from a preset (an older backend without `preset` counts when it has the preset's name)
  const backendsOf = (id, providers) => (providers || []).filter(p => p.preset === id || (!p.preset && p.name === id));

  // GET /api/backends/detect rows as cards under the headings CLI, Cloud and Local. `providers` are the saved backends:
  // a card whose preset is already one offers "Edit <name>" beside "Add".
  function renderPresetCards(rows, providers) {
    rows = rows || [];
    const card = (r) => {
      const state = [r.found ? '<span class="ok">found</span>' : '<span class="muted">not found</span>'];
      if (r.version) state.push(esc(r.version));
      if (r.where) state.push(`at ${esc(r.where)}`);
      if (r.server) state.push(`server answers at ${esc(r.server)}`);
      if (r.signed_in === "yes") state.push("signed in");
      if (r.key_env) state.push(r.key_set ? `<span class="ok">${esc(r.key_env)} set</span>` : `${esc(r.key_env)} not set`);
      const edits = backendsOf(r.preset, providers).map(p =>
        `<button type="button" class="secondary small-btn" data-edit-backend="${esc(p.name)}">Edit ${esc(p.name)}</button>`).join(" ");
      return `<div class="preset-card${r.found ? " found" : ""}" data-card="${esc(r.preset)}">
        <p><b>${esc(r.label)}</b> ${localityChip(r.locality)}${r.experimental ? " " + EXPERIMENTAL : ""}</p>
        <p class="small">${state.join(" · ")}</p>
        <p class="small">Sends to: ${esc(r.destination || "?")}</p>
        <p class="muted small">${esc(r.billing || "")}</p>
        ${r.telemetry ? `<p class="muted small" data-telemetry>${esc(r.telemetry)}</p>` : ""}
        ${r.warning ? `<p class="small warn-text">${esc(r.warning)}</p>` : ""}
        ${r.checked ? `<p class="muted small">${esc(r.checked)}</p>` : ""}
        <div class="row">${edits} <button type="button" class="secondary small-btn" data-preset-add="${esc(r.preset)}">Add</button></div></div>`;
    };
    const groups = FAMILIES.map(([fam, title]) => {
      const mine = rows.filter(r => r.family === fam);
      return mine.length ? `<h3>${title}</h3><div class="preset-cards">${mine.map(card).join("")}</div>` : "";
    }).join("");
    const none = rows.some(r => r.found) ? "" : `<p class="muted" data-nothing-found>Nothing was found on this computer:
      no CLI is installed, no local server answers and no key variable is set. You can still add an AI model by hand with
      "Add", or install one first; WIDER installs nothing.</p>`;
    return none + groups;
  }

  const cmdText = (c) => (c ? JSON.stringify(c) : "");
  // How to sign a CLI tool in, from its `auth_hint` ("X is not signed in: run ..."), shown in the form, not only under
  // Advanced (M27)
  function signinText(extra) {
    const hint = String((extra || {}).auth_hint || "").trim();
    if (!hint) return "";
    const i = hint.indexOf(": ");
    return `Sign-in: ${i >= 0 ? hint.slice(i + 2) : hint}.`;
  }
  // The Add form filled from a preset (GET /api/providers/presets row): the name is the preset id, or the first free
  // `<id>-N` when a backend already has it.
  function formValuesFromPreset(p, providers) {
    return { mode: "add", title: "Add AI model", preset: p.id, name: freeName(p.id, (providers || []).map(x => x.name)),
      kind: p.kind, model: p.default_model || "", base_url: p.base_url || "", api_key_env: p.api_key_env || "",
      command: cmdText(p.command), timeout_s: p.timeout_s ?? "", max_concurrency: "", temperature: "",
      price_input_per_mtok: "", price_output_per_mtok: "", price_cached_input_per_mtok: "", price_cache_write_per_mtok: "",
      extra: { ...(p.extra || {}) }, experimental: !!p.experimental, warning: p.warning || "", signin: signinText(p.extra) };
  }
  // The Edit form of a saved backend (GET /api/providers row): every current value, so Save changes only what was typed.
  function formValuesFromBackend(b) {
    const v = (x) => (x == null ? "" : x);
    return { mode: "edit", title: `Edit ${b.name}`, preset: b.preset || null, name: b.name, kind: b.kind, model: v(b.model),
      base_url: v(b.base_url), api_key_env: v(b.api_key_env), command: cmdText(b.command), timeout_s: v(b.timeout_s),
      max_concurrency: v(b.max_concurrency), temperature: v(b.temperature),
      price_input_per_mtok: v(b.price_input_per_mtok), price_output_per_mtok: v(b.price_output_per_mtok),
      price_cached_input_per_mtok: v(b.price_cached_input_per_mtok), price_cache_write_per_mtok: v(b.price_cache_write_per_mtok),
      extra: { ...(b.extra || {}) }, experimental: false, warning: "", signin: signinText(b.extra) };
  }
  // The embedding form: Add (no row) or Edit (GET /api/embeddings row).
  function embeddingFormValues(e) {
    const v = (x) => (x == null ? "" : x);
    if (!e) return { mode: "add", title: "Add embedding model", name: "", kind: "sentence_transformers", model: "", dim: "",
      batch_size: "", base_url: "", api_key_env: "", price_input_per_mtok: "" };
    return { mode: "edit", title: `Edit ${e.name}`, name: e.name, kind: e.kind, model: v(e.model), dim: v(e.dim),
      batch_size: v(e.batch_size), base_url: v(e.base_url), api_key_env: v(e.api_key_env),
      price_input_per_mtok: v(e.price_input_per_mtok) };
  }

  // The model dropdown of the form: the catalogue's suggestions (and the current model), a "Load models from …"
  // button and a free-text entry. The list loads by itself only from a loopback host or a CLI's own catalogue; a cloud
  // or other host is asked only when the button is pressed.
  function modelOptions(values, preset) {
    const url = values.base_url || (values.kind === "ollama" ? "http://127.0.0.1:11434" : "");
    const suggested = [...new Set([values.model, ...((preset && preset.models) || [])].filter(Boolean))];
    const where = preset && preset.family === "cloud" ? (preset.vendor || preset.label)
      : (url ? (isLoopback(url) ? "this computer" : hostOf(url)) : ((preset && preset.label) || values.kind));
    const autoLoad = url ? isLoopback(url) : values.kind === "cli_agent";
    return { suggested, loadLabel: `Load models from ${where}`, autoLoad, freeText: true };
  }

  // 409 provider_exists / embedding_exists: what a replace would reset, with Replace, Edit instead and Cancel.
  function resetFieldsText(d) {
    const f = (d.reset_fields || []);
    return `${d.name} already exists. Replacing it resets ${f.length ? f.join(", ") : "nothing"} to the defaults; ` +
      "Edit instead changes only what you typed and keeps the rest.";
  }
  function replaceDialogHtml(d) {
    return `<h2 id="choice-title">Replace ${esc(d.name)}?</h2><p>${esc(resetFieldsText(d))}</p>
      <div class="row"><button type="button" data-act="replace">Replace</button>
      <button type="button" class="secondary" data-act="edit">Edit instead</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }

  // POST /api/estimate: the range, its assumptions and the destination, as the CLI's --estimate-only prints them.
  function estimateText(r) {
    if (!r) return "";
    const lines = r.lines || [`Estimated cost: ${r.priced ? `${usd(r.min_usd)}–${usd(r.max_usd)}` : "not priced"} for ${n(r.calls)} calls to ${r.destination}`]
      .concat((r.assumptions || []).map(a => `  assumption: ${a}`));
    return lines.map(l => (l.startsWith("  assumption: ") ? `<span class="muted small">${esc(l.trim())}</span>` : esc(l))).join("<br>");
  }
  // "Embed corpus" on a priced embedding model: the range first, with Continue and Cancel.
  function embedEstimateHtml(name, r) {
    return `<h2 id="choice-title">Embed the corpus with ${esc(name)}?</h2><p>${estimateText(r)}</p>
      <div class="row"><button type="button" data-act="continue">Continue</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }

  // Before "Embed corpus": a priced embedding model shows its range first (Continue / Cancel); true starts the job.
  // `api` and `ask` (askChoice) are passed in, so tests run it under Node.
  async function confirmEmbedCost(emb, api, ask) {
    if (!emb || !(Number(emb.price_input_per_mtok) > 0)) return true;
    const r = await api("/api/estimate", { method: "POST", body: JSON.stringify({ stage: "embed", embedding: emb.name }) });
    return !!(await ask(embedEstimateHtml(emb.name, r)));
  }

  // The home banner and the Settings text of "Check backends" (`wider check-backends`).
  function checkBannerText() {
    return "The check sends one fixed test request (fixed test sentences, no project data) to each checked model; " +
      "on a paid model it may cost a little.";
  }

  // GET /api/providers/consent or /api/embeddings/consent: the consented operations of each backend that needs consent,
  // each with "Revoke" (data-revoke, data-revoke-kind).
  function consentCardHtml(rows, kind) {
    const need = (rows || []).filter(r => r.needs_consent);
    if (!need.length) return "";
    const what = kind === "embedding" ? "embedding services" : "AI models";
    return `<table class="check-table"><tr><th>${what} off this computer</th><th>sends to</th><th>allowed for</th><th></th></tr>
      ${need.map(r => { const ops = (r.operations || []).map(o => esc(o)).join(", "); return `<tr tabindex="-1" data-focus-key="consent-${esc(kind)}-${esc(r.name)}"><td>${esc(r.name)}</td><td>${esc(r.destination)}</td>
      <td>${ops || '<span class="muted">nothing yet (asks first)</span>'}${r.stale ? ' <span class="chip warn">changed; asks again</span>' : ""}</td>
      <td>${ops ? `<button type="button" class="secondary small-btn" data-revoke="${esc(r.name)}" data-revoke-kind="${esc(kind)}">Revoke</button>` : ""}</td></tr>`; }).join("")}</table>`;
  }

  // Settings and data, at the top (owner inspection S2, mock redesign-c): what leaves this computer, grouped by the
  // step it belongs to, from the model each step uses and where that model runs, and the prior-review lookup policy;
  // a lead sentence names how many of them send text away. ``look`` is project.yaml's online_lookups: every online
  // lookup is listed with its switch, and Ask WIDER with its model (inspection 1 Nov: the list named only the models)
  const MAILTO = ", with your contact address when WIDER_MAILTO is set";
  function whatLeavesHtml(provs, embs, pol, look) {
    look = look || {};
    const byName = {};
    ((provs && provs.providers) || []).forEach(p => { byName[p.name] = p; });
    const sp = (provs && provs.stage_providers) || {};
    const away = (b) => !!b && (b.locality === "cloud" || b.locality === "network");
    const where = (b) => !b ? "no model chosen yet: nothing is sent"
      : b.locality === "local" ? `${b.name}, on this computer: nothing leaves it`
      : b.locality === "test" ? `${b.name}, a test model: nothing is sent`
      : b.locality === "network" ? `${b.name}: the texts go to ${b.destination || "a computer on your network"}`
      : b.locality === "cloud" ? `${b.name}: the texts go to ${b.destination || "a remote service"}, only after you allow it`
      : `${b.name}: where it runs is not checked yet (Check models, below)`;
    const llm = (stage) => byName[sp[stage]] || byName[provs && provs.default];
    const emb = embs && (embs.embeddings || []).find(e => e.name === embs.default);
    const screenB = byName[sp.screen_b] || llm("screen"), judge = byName[sp.screen_judge] || llm("screen");
    const groups = [
      ["1 Literature search", [["Model for query creation", llm("construct"),
        !sp.construct && llm("construct") ? `${where(llm("construct"))} (the project default; no model chosen on Model for query creation yet)` : null, away(llm("construct"))], ["Checking search strings", byName[sp.review] || llm("construct")],
        ["Search terms and whole queries to OpenAlex (Count the query, term counts)", null, look.terms_allowed
          ? "on: the terms or the query string go to OpenAlex, which counts the works they find" : "off: no search term or query is sent", !!look.terms_allowed],
        ["Seed papers to OpenAlex, Crossref, Europe PMC and Semantic Scholar (finding them, their abstracts, the seed check of Count the query)", null, look.allowed
          ? `on: each paper's DOI and title go to these services${MAILTO}` : "off: nothing is sent", !!look.allowed],
        ["Prior-review studies to OpenAlex", null, pol && pol.allow_openalex_lookup ? "on: their DOIs and titles go to OpenAlex" : "off: nothing is sent", !!(pol && pol.allow_openalex_lookup)]]],
      ["2 Data cleaning", [["Missing abstracts to OpenAlex, Crossref, Europe PMC and Semantic Scholar", null, look.allowed
          ? `on: each record's DOI and title go to these services${MAILTO}` : "off: nothing is sent; Missing abstracts asks first", !!look.allowed]]],
      ["3 Model tuning and validation", [["Embedding model for Ranking", emb]]],
      ["4 Screening and outputs", [["Screening, pass A", llm("screen")], ["Screening, pass B", screenB], ["Screening, judge", judge]]],
      ["Any page", [["Ask WIDER (your question and WIDER's documents, never a record)", byName[sp.construct] || null,
        sp.construct ? null : "no model chosen yet: nothing is sent"]]]];
    const out = [];
    const body = groups.map(([g, rows]) => `<tr><th colspan="2" scope="colgroup" class="leaves-group">${esc(g)}</th></tr>`
      + rows.map(([k, b, text, sends]) => {
        const leaves = text != null ? sends : away(b);
        if (leaves) out.push(k.replace(/ \(.*\)$/, ""));
        return `<tr${leaves ? ' class="leaves-out"' : ""}><th scope="row">${esc(k)}</th><td>${esc(text != null ? text : where(b))}${leaves ? ' <span class="chip loc-cloud">leaves this computer</span>' : ""}</td></tr>`;
      }).join("")).join("");
    const lead = out.length ? `<b>${out.length} ${out.length === 1 ? "thing leaves" : "things leave"} this computer:</b> ${esc(out.join(", "))}.`
      : "<b>Nothing leaves this computer</b> with the models chosen now.";
    return `<section class="leaves-box" id="settings-leaves" aria-labelledby="leaves-h"><h2 id="leaves-h">What leaves this computer</h2>
      <p>${lead} A model off this computer and every online lookup ask before the first text is sent.</p>
      <table class="summary-table"><tbody>${body}</tbody></table></section>`;
  }

  // Settings: every backend with Edit (an LLM backend's form opens in place on Settings, an embedding model's on its
  // Ranking card) and Remove, the model per stage (an empty choice is the project default), then the consent cards
  // with "Revoke".
  function settingsBackendsHtml(provs, embs, pcons, econs) {
    // where the backend runs, from the same row as its picker card; one never checked reads "unknown, press Check"
    const WHERE = { local: "local", cloud: "remote", network: "local network", test: TEST_MODEL };
    const whereChip = (b) => !b.locality ? "" : ` <span class="chip loc-${esc(b.locality)}" data-locality="${esc(b.locality)}">${esc(
      b.locality === "unknown" ? (b.locality_label || "unknown") : (WHERE[b.locality] || b.locality_label || b.locality))}</span>`;
    // one card per backend (owner, 29 Oct: Settings option A): its name and where it runs, what it is, and full-size
    // Edit and Remove buttons
    const row = (role, b, isDefault, uses) => `<article class="bk-card"><p class="bk-top"><b>${esc(b.name)}</b>${whereChip(b)} <span class="bk-role">${esc(role === "LLM" ? "language model" : "embedding model")}</span>${isDefault ? ' <span class="chip ok">default</span>' : ""}</p>
      <dl class="bk-meta"><div><dt>kind</dt><dd>${esc(b.kind === "fake" ? "test model" : b.kind)}</dd></div><div><dt>model</dt><dd>${esc(b.model || "")}</dd></div>${(uses || []).length ? `<div><dt>used for</dt><dd>${uses.map(u => `<span class="chip">${esc(u)}</span>`).join(" ")}</dd></div>` : ""}</dl>
      <div class="bk-acts"><button type="button" class="secondary" data-settings-edit="${esc(b.name)}" data-settings-role="${esc(role)}" aria-label="Edit ${esc(b.name)}">Edit</button>
      <button type="button" class="secondary danger" data-settings-remove="${esc(b.name)}" data-settings-role="${esc(role)}" aria-label="Remove ${esc(b.name)}">Remove</button></div></article>`;
    const list = (provs ? provs.providers.map(p => row("LLM", p, p.default, p.stages)) : [])
      .concat(embs ? embs.embeddings.map(e => row("embedding", e, e.name === embs.default, [])) : []).join("");
    // the review row (2c D0c Q3) has two selects, `review` and `review_2`: its empty choices are the construct backend
    // and the first reader again, never the project default
    // the screen row (configuration comparison D10) has three selects, `screen`, `screen_b` and `screen_judge`: pass B
    // and the judge start with "Same as pass A", as `wider providers stage screen_b` without a name leaves them
    const EMPTY = { review: "Same as building search terms, read twice", review_2: "Same as reader 1",
      screen_b: "Same as pass A", screen_judge: "Same as pass A" };
    const STAGE_NAME = { construct: "Building search terms", screen: "Screening, pass A (first read)",
      screen_b: "Screening, pass B (second read)", screen_judge: "Screening, judge (breaks a tie)",
      review: "Checking search strings, reader 1", review_2: "Checking search strings, reader 2" };
    const ROW_KEYS = { screen: ["screen", "screen_b", "screen_judge"], review: ["review", "review_2"] };
    // one row per stage key (UX review F18): a plain stage name in normal colour, then its select
    const select = (stage) => {
      const cur = (provs.stage_providers || {})[stage] || "";
      const empty = EMPTY[stage] || `project default${provs.default ? ` (${provs.default})` : ""}`;
      return `<select id="settings-stage-${esc(stage)}" data-settings-stage="${esc(stage)}">
        <option value=""${cur ? "" : " selected"}>${esc(empty)}</option>
        ${provs.providers.map(p => `<option value="${esc(p.name)}"${p.name === cur ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select>`;
    };
    const tr = (stage, row) => `<tr${row ? ` data-settings-stage-row="${esc(row)}"` : ""}><th scope="row"><label for="settings-stage-${esc(stage)}">${esc(STAGE_NAME[stage] || stage)}</label></th><td>${select(stage)}</td></tr>`;
    const stageRows = (stage) => (ROW_KEYS[stage] || [stage]).map((k, i) => tr(k, i === 0 ? stage : "")).join("");
    const stages = provs && provs.providers.length ? `<table class="summary-table settings-stage-table" id="settings-stages">
      <caption>AI model per stage</caption><thead><tr><th scope="col">Stage</th><th scope="col">Model</th></tr></thead>
      <tbody>${(provs.stages || []).map(stageRows).join("")}</tbody></table>
      <p class="small">A stage with no model chosen uses the project default. Checking the search strings reads them twice with the search-terms model unless you choose readers.</p>
      ${cmdFold("<code>wider providers stage STAGE NAME</code> (review: <code>wider providers stage review NAME [NAME2]</code>; screening's pass B and judge: <code>wider providers stage screen_b NAME</code>, <code>screen_judge NAME</code>)")}` : "";
    return `<div class="bk-cards" id="settings-backend-list">${list}<button type="button" class="bk-add" data-find-backends>+ Add or find AI models</button></div>
      ${stages}<div id="settings-backend-form" class="card-inset" hidden></div>
      <div id="settings-consent">${consentCardHtml(pcons, "llm")}${consentCardHtml(econs, "embedding")}</div>`;
  }

  // Advanced headers: a sensitive header (its name holds key, token, auth, secret or cookie) takes the NAME of an
  // environment variable and is saved as `{env:NAME}`; its value never goes into the project.
  const SENSITIVE_HEADER_WORDS = ["key", "token", "auth", "secret", "cookie"];
  const isSensitiveHeader = (h) => SENSITIVE_HEADER_WORDS.some(w => String(h || "").toLowerCase().includes(w));
  const ENV_REF = /^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$/;
  function headerFieldHtml(name, value, i) {
    const sens = isSensitiveHeader(name), ref = (String(value || "").match(ENV_REF) || [])[1];
    return `<span class="row" data-header-row="${i}"><input data-header-name aria-label="Header name" value="${esc(name || "")}" placeholder="header name">
      ${sens ? `<input data-header-value aria-label="Environment variable that holds ${esc(name)}" value="${esc(ref || "")}" placeholder="environment variable name, e.g. MY_TOKEN">
      <span class="muted small">a variable name only; its value is read from the environment</span>`
        : `<input data-header-value aria-label="Value of ${esc(name || "header")}" value="${esc(value || "")}" placeholder="value">`}</span>`;
  }
  function headerValue(name, typed) {
    const t = String(typed || "").trim();
    if (!isSensitiveHeader(name) || !t) return t;
    if (ENV_REF.test(t)) return t;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(t)) throw new Error(`${name} takes the name of an environment variable, not its value`);
    return `{env:${t}}`;
  }

  // Advanced `extra` settings as "key=value" lines (the value as JSON when it parses), like `--extra key=value`.
  // `headers` is edited apart. On an edit, a key that was removed is sent as "" (the server removes it).
  function extraToLines(extra) {
    return Object.entries(extra || {}).filter(([k]) => k !== "headers")
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join("\n");
  }
  function linesToExtra(text, old) {
    const out = {};
    for (const raw of String(text || "").split("\n")) {
      const line = raw.trim(); if (!line) continue;
      const i = line.indexOf("=");
      if (i < 1) throw new Error(`extra settings take key=value, got "${line}"`);
      const k = line.slice(0, i).trim(), v = line.slice(i + 1).trim();
      try { out[k] = JSON.parse(v); } catch (e) { out[k] = v; }
    }
    for (const k of Object.keys(old || {})) if (k !== "headers" && !(k in out)) out[k] = "";
    return out;
  }

  return { backendChangeHtml, newSetHtml, modelsCardHtml, setsCardHtml, useRefusalHtml, compareHtml, mixedBadge, expBadge,
    renderPresetCards, formValuesFromPreset, formValuesFromBackend, embeddingFormValues, modelOptions, isLoopback, hostOf,
    resetFieldsText, replaceDialogHtml, estimateText, embedEstimateHtml, confirmEmbedCost, checkBannerText, consentCardHtml, settingsBackendsHtml, whatLeavesHtml,
    isSensitiveHeader, headerFieldHtml, headerValue, extraToLines, linesToExtra, freeName };
})();
if (typeof module !== "undefined" && module.exports) module.exports = Backends;
