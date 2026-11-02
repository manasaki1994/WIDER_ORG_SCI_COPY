// Model picker cards, one builder for the model page (LLM), the Ranking page (embedding model) and the Screening page
// (LLM). Rows come unchanged from GET /api/providers and GET /api/embeddings, the same rows `wider providers list
// --json` and `wider embed list --json` print. cardsHtml builds HTML only (no DOM, no requests), so tests run it under
// Node; every value goes through `esc` (common.js). A key is named by its variable, never by its value.
const Pickers = (() => {
  const CHIP = { local: "local", cloud: "remote", network: "local network", test: "Test model (not for real reviews)" };
  const TIP = { local: "nothing leaves this computer", test: "A stand-in model for practice. It gives no real answers.", unknown: "where the text goes is not known until it is checked" };
  const when = (t) => esc(fmtWhen(t));

  function chipHtml(r) {
    const loc = r.locality || "unknown";
    const word = loc === "unknown" ? (r.locality_label || "unknown") : (CHIP[loc] || r.locality_label || loc);
    const tip = TIP[loc] || (r.destination ? `text is sent to ${r.destination}` : "text is sent off this computer");
    return `<span class="chip loc-${esc(loc)}" title="${esc(tip)}" data-locality="${esc(loc)}">${esc(word)}</span>`;
  }
  function keyHtml(r) {
    if (!r.api_key_env) return "";
    return r.key_set ? `<span class="ok" data-key>${esc(r.api_key_env)} set</span>` : `<span class="bad" data-key>${esc(r.api_key_env)} MISSING</span>`;
  }
  function checkHtml(c) {
    if (!c) return '<span class="muted" data-last-check>not checked yet</span>';
    const res = c.ok ? '<span class="ok">ok</span>' : `<span class="bad">failed</span>${c.error ? ` · ${esc(c.error)}` : ""}`;
    return `<span data-last-check>last check ${when(c.at)}: ${res}</span>`;
  }
  function weightsHtml(r) {
    if (r.kind !== "sentence_transformers" || r.downloaded == null) return "";
    return r.downloaded ? '<span class="ok" data-weights>downloaded</span>'
      : `<span class="muted" data-weights>not downloaded</span>${r.size_mb ? ` (about ${esc(r.size_mb)} MB)` : ""}`;
  }
  // `actions`: the buttons beside Choose, in order: "check", "download" (local embedding without weights only),
  // "edit", "remove". Each is a `data-pact` button with `data-name`; its result goes to `[data-presult]`.
  const ACTION_LABEL = { check: "Check", download: "Download", edit: "Edit", remove: "Remove" };
  function actionsHtml(r, actions) {
    return (actions || []).filter(a => a !== "download" || (r.kind === "sentence_transformers" && r.downloaded === false))
      .map(a => `<button type="button" class="secondary small-btn" data-pact="${a}" data-name="${esc(r.name)}" aria-label="${ACTION_LABEL[a]} ${esc(r.name)}">${ACTION_LABEL[a]}${a === "download" && r.size_mb ? ` (~${esc(r.size_mb)} MB)` : ""}</button>`)
      .join(" ");
  }

  // One card per row: name, model, locality chip, destination, key state, last check, and a Choose button; the
  // chosen card shows a "Chosen" chip in its place. `kind` is "llm" or "embedding" (it names the cards for screen readers).
  function cardsHtml(rows, { kind, chosen, actions } = {}) {
    rows = rows || [];
    const what = kind === "embedding" ? "embedding model" : "model";
    if (!rows.length) return `<p class="muted" data-no-cards>No ${what} is set up yet.</p>`;
    const card = (r) => {
      const on = r.name === chosen;
      const facts = [keyHtml(r), weightsHtml(r), r.dim ? `${esc(r.dim)} dimensions` : ""].filter(Boolean).join(" · ");
      const uses = (r.stages || []).map(s => ` <span class="chip">${esc(s)}</span>`).join("");
      return `<div class="preset-card picker-card${on ? " chosen" : ""}" data-picker-card="${esc(r.name)}">
        <p><b>${esc(r.name)}</b> ${chipHtml(r)}${r.default ? ' <span class="chip">default</span>' : ""}${uses}</p>
        <p class="small">${esc(r.model || "")}</p>
        <p class="small">Sends to: ${esc(r.destination || "?")}${(actions || []).includes("check")   // this card's consent state (D29)
          ? ` <span class="send-short" data-send="backend_check" data-send-backend="${esc(r.name)}" data-send-part="consent_line" hidden></span>` : ""}</p>
        ${facts ? `<p class="small">${facts}</p>` : ""}
        <p class="small">${checkHtml(r.last_check)}</p>
        <p class="small" data-presult="${esc(r.name)}" role="status"></p>
        <div class="row">${on   // the chosen card shows a state chip, not a button (D32 F15)
          ? `<span class="chip chip-chosen" data-chosen="${esc(r.name)}" tabindex="-1" aria-label="Chosen ${what} ${esc(r.name)}">Chosen</span>`
          : `<button type="button" class="secondary small-btn" data-pick="${esc(r.name)}" aria-label="Choose ${what} ${esc(r.name)}">Choose</button>`}
          ${actionsHtml(r, actions)}</div></div>`;
    };
    // what Check sends and why, said once above the cards (owner, 29 Oct: the same box on every card was too much, D29)
    const once = (actions || []).includes("check") ? `<p class="send-note" data-send="backend_check" data-send-backend="${esc(rows[0].name)}" data-send-part="general" hidden></p>` : "";
    // test models stay hidden behind "Show test models" unless chosen (D32 F14, owner 30 Oct): researchers do not
    // pick a stand-in for a real review by accident; the switch shows them for practice
    const hide = (r) => r.locality === "test" && r.name !== chosen;
    const nHidden = rows.filter(hide).length;
    const cards = rows.map(r => hide(r) ? card(r).replace('" data-picker-card=', '" hidden data-test-card data-picker-card=') : card(r)).join("");
    const toggle = nHidden ? `<p><button type="button" class="link-btn" data-show-test aria-expanded="false">Show test models (${nHidden})</button></p>` : "";
    return once + `<div class="preset-cards" role="group" aria-label="${what === "model" ? "Models" : "Embedding models"}">${cards}</div>` + toggle;
  }

  // Choose buttons call onChoose(name, button); the action buttons call onAction(act, name, button) when given.
  function wire(box, onChoose, onAction) {
    box.querySelectorAll("[data-show-test]").forEach(b => b.onclick = () => {
      const show = b.getAttribute("aria-expanded") !== "true";
      box.querySelectorAll("[data-test-card]").forEach(c => { c.hidden = !show; });
      b.setAttribute("aria-expanded", show ? "true" : "false");
      b.textContent = b.textContent.replace(show ? "Show" : "Hide", show ? "Hide" : "Show");
    });
    box.querySelectorAll("[data-pick]").forEach(b => b.onclick = () => onChoose(b.dataset.pick, b));
    if (onAction) box.querySelectorAll("[data-pact]").forEach(b => b.onclick = () => onAction(b.dataset.pact, b.dataset.name, b));
  }

  // ------------------------------------------------------------------ LLM picker (Screening page)
  // Cards from GET /api/providers, preselecting the stage's backend (`stage_providers[stage]`), else the project
  // default. Choosing marks the card for the next run only: nothing is saved, as `wider screen run --provider NAME`
  // saves nothing. `keep` (the card picked before a redraw) stays picked while it exists. Opening it sends one GET and
  // nothing else. onPick(row) gets the row of the picked card.
  async function llmPicker(box, { stage = "screen", keep = null, onPick } = {}) {
    const d = await api("/api/providers");
    const rows = d.providers || [], has = (n) => n && rows.some(r => r.name === n);
    const chosen = [keep, (d.stage_providers || {})[stage], d.default].find(has) || null;
    const rowOf = (n) => rows.find(r => r.name === n) || null;
    const draw = (name) => {
      box.innerHTML = `<h2>Model for this run</h2>
        <p class="muted">The chosen card screens the next run only; it changes no setting. The card marked
          <b>${esc(stage)}</b> is the model set for this stage on Settings.</p>
        ${cardsHtml(rows, { kind: "llm", chosen: name })}`;
      wire(box, (n) => {
        draw(n);
        const b = box.querySelector("[data-chosen]");
        if (b && b.focus) b.focus();   // the redraw replaced the pressed button with the Chosen chip; the keyboard stays on its card
        if (onPick) onPick(rowOf(n));
      });
    };
    draw(chosen);
    return { rows, chosen, row: rowOf(chosen), stages: d.stage_providers || {} };
  }
  // ------------------------------------------------------------------ pass pickers (Screening page, design D14)
  // In protocol and second-screen mode: pass B and the judge (second screen: reads S2 and S3) each pick a backend for
  // the next run, "Same as pass A" first; nothing is saved, as --provider-b and --provider-c save nothing. `view` is
  // GET /api/screen/passes: a pass that does not run has its picker disabled with the reason beside it, and the
  // sentences naming passes that share a model follow in normal text. `picks` = {B, C}: a backend name or null.
  function passPickersHtml(rows, view, picks = {}) {
    const second = view.mode === "second";
    const label = { B: second ? "Pass B (read S2)" : "Pass B", C: second ? "Judge (read S3)" : "Judge" };
    const off = view.pass_off || {}, aName = (view.passes || {}).A;
    const sel = (p) => {
      const cur = off[p] ? "" : (picks[p] || "");
      const opts = [`<option value=""${cur ? "" : " selected"}>Same as pass A</option>`].concat(rows.filter(r => r.locality !== "test" || r.name === cur).map(r =>
        `<option value="${esc(r.name)}"${r.name === cur ? " selected" : ""}>${esc(r.name)} (${esc(r.model || "?")})</option>`));
      return `<div class="row"><label class="inline">${label[p]} <select data-pass-pick="${p}"${off[p]
        ? ` disabled aria-describedby="sc-pass-off-${p}"` : ""}>${opts.join("")}</select></label>${off[p]
        ? ` <span id="sc-pass-off-${p}" data-pass-off="${p}">${esc(off[p])}</span>` : ""}</div>`;
    };
    return `<h2>Models of the other passes</h2>
      <p class="muted">Pass A runs on the card chosen above${aName ? ` (${esc(aName)})` : ""}. These picks screen the
        next run only; they change no setting. Settings sets the defaults (stages screen_b and screen_judge).</p>
      ${sel("B")}${sel("C")}
      ${(view.shared || []).map(s => `<p data-shared>${esc(s)}</p>`).join("")}`;
  }
  // The ScreenRun / estimate fields of the picks: "Same as pass A" sends pass A's name, so a stage default set on
  // Settings does not apply instead; a pass that does not run sends nothing.
  function passFields(view, picks, aName) {
    const out = {}, off = (view && view.pass_off) || {};
    for (const [p, key] of [["B", "provider_b"], ["C", "provider_c"]]) if (!off[p]) out[key] = (picks || {})[p] || aName || null;
    return out;
  }

  // The line next to Run: where the picked backend runs and what a run costs, from the same row.
  function runLine(r) {
    if (!r) return "Choose a model above first.";
    const loc = r.locality || "unknown";
    const word = loc === "unknown" ? (r.locality_label || "unknown") : (CHIP[loc] || r.locality_label || loc);
    const where = loc === "local" || loc === "test" ? word : `${word}, sends to ${r.destination || "?"}`;
    const usd = (v) => `$${Number(v)}`;
    const cost = loc === "local" || loc === "test" ? "no charge"
      : r.price_input_per_mtok != null || r.price_output_per_mtok != null
        ? `${usd(r.price_input_per_mtok ?? 0)} input and ${usd(r.price_output_per_mtok ?? 0)} output per million tokens`
        : "not priced";
    return `Runs on ${r.name} (${r.model || "?"}): ${where}, ${cost}.`;
  }

  // ------------------------------------------------------------------ embedding picker (Ranking page)
  // Cards from GET /api/embeddings with Check, Download (asks first), Edit and Remove, the add and edit form, and
  // Choose = POST /api/embeddings/default (`wider embed set-default`), the model Embed corpus and Rank use. Opening it
  // sends one GET and nothing else: no download, probe or check. Check goes through api(), so a backend off this
  // computer without consent opens the consent dialog before anything is sent. Revoking consent is on Settings.
  const opt = (values, current) => values.map(v => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(v)}</option>`).join("");
  const numOrNull = (v) => (String(v).trim() === "" ? null : Number(v));
  const flash = (el, msg, bad) => { if (!el) return; el.innerHTML = bad ? `<span class="bad">${esc(msg)}</span>` : esc(msg); };
  const changed = (r) => { window.dispatchEvent(new CustomEvent("wider:changed")); return r; };
  const send = (path, method, body) => api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }).then(changed);
  let embChecked = null;   // the last Check or Download result, kept across the redraw that shows its new last-check line

  async function embeddingPicker(box, opts = {}) {
    const d = await api("/api/embeddings");
    const chosen = d.embeddings.some(e => e.name === d.default) ? d.default : null;
    const q = (s) => box.querySelector(s);
    box.innerHTML = `<h2>Embedding model</h2>
      <p class="muted">The chosen card is the project's embedding model: Embed corpus and Rank use it. A local
        sentence-transformers model is downloaded only when you press Download; an API model needs its key variable set.</p>
      ${cardsHtml(d.embeddings, { kind: "embedding", chosen, actions: ["check", "download", "edit", "remove"] })}
      <span class="muted small" id="embedding-msg" role="status"></span>
      <div class="row"><button type="button" class="secondary" id="embedding-add">Add an embedding model</button></div>
      <div id="embedding-form-box" class="card-inset" hidden>
        <h3 id="embedding-form-title" tabindex="-1">Add embedding model</h3>
        <form id="embedding-form" class="form-grid">
          <label>Name <input name="name" required></label>
          <label>Kind <select name="kind">${opt(d.kinds || [])}</select></label>
          <label>Model <input name="model" placeholder="Hugging Face id, or the API's model name"></label>
          <label>Dimensions (optional truncation) <input name="dim" type="number" min="8"></label>
          <label>Batch size <input name="batch_size" type="number" min="1" step="1"></label>
          <label>Base URL (models reached over HTTP) <input name="base_url"></label>
          <label>API key environment variable (name only) <input name="api_key_env"></label>
          <label>Price in US dollars per million input tokens (optional, for the cost range) <input name="price_input_per_mtok" type="number" min="0" step="any"></label>
          <label class="inline"><input type="checkbox" name="set_default"> make default</label>
          <div class="row"><button type="submit" class="secondary">Save embedding</button> <button type="button" class="secondary" id="embedding-cancel">Cancel</button></div>
          <span class="muted small" id="embedding-form-msg" role="status"></span>
        </form>
      </div>`;
    const again = async () => { await embeddingPicker(box, opts); if (opts.onChanged) await opts.onChanged(); };
    const msg = () => q("#embedding-msg");
    const wrap = (fn, el) => async (...a) => { try { await fn(...a); } catch (e) { flash(typeof el === "function" ? el() : el, e.message, true); } };
    const form = q("#embedding-form"), formBox = q("#embedding-form-box");
    let state = null;
    const openForm = (v) => {
      state = v; form.reset(); q("#embedding-form-msg").textContent = "";
      q("#embedding-form-title").textContent = v.title;
      form.name.value = v.name; form.name.readOnly = v.mode === "edit";
      form.kind.value = v.kind; form.kind.disabled = v.mode === "edit";
      for (const k of ["model", "dim", "batch_size", "base_url", "api_key_env", "price_input_per_mtok"]) form[k].value = v[k] ?? "";
      formBox.hidden = false; q("#embedding-form-title").focus();
    };
    form.onsubmit = wrap(async (ev) => {
      ev.preventDefault();
      const name = form.name.value.trim();
      const b = { model: form.model.value.trim() || null, dim: numOrNull(form.dim.value), batch_size: numOrNull(form.batch_size.value),
        base_url: form.base_url.value.trim() || null, api_key_env: form.api_key_env.value.trim() || null,
        price_input_per_mtok: numOrNull(form.price_input_per_mtok.value) };
      if (state.mode === "edit") {
        await send(`/api/embeddings/${encodeURIComponent(state.name)}`, "PATCH", b);
        if (form.set_default.checked) await send("/api/embeddings/default", "POST", { name: state.name });
      } else {
        const add = { ...b, model: b.model || "", name, kind: form.kind.value, set_default: form.set_default.checked };
        try { await send("/api/embeddings", "POST", add); } catch (e) {
          if (e.status !== 409 || !e.detail || e.detail.error !== "embedding_exists") throw e;
          const c = await askChoice(Backends.replaceDialogHtml(e.detail));
          if (!c) return;
          if (c.act === "edit") { openForm(Backends.embeddingFormValues(d.embeddings.find(x => x.name === name))); return; }
          await send("/api/embeddings", "POST", { ...add, replace: true });
        }
      }
      await again();
      flash(msg(), `saved ${name}`);
    }, () => q("#embedding-form-msg"));
    q("#embedding-add").onclick = () => openForm(Backends.embeddingFormValues(null));
    q("#embedding-cancel").onclick = () => { form.reset(); formBox.hidden = true; state = null; q("#embedding-add").focus(); };
    wire(box, wrap(async (name) => {
      if (name === chosen) return;
      await send("/api/embeddings/default", "POST", { name });
      await again();
      flash(msg(), `${name} is now the embedding model for Embed corpus and Rank.`);
    }, msg), wrap(async (act, name, b) => {
      const cell = box.querySelector(`[data-presult="${CSS.escape(name)}"]`);
      if (act === "check" || act === "download") {
        const dl = act === "download";
        if (dl && !(await askConfirm(`Download the weights for ${name} now? This can take a while and needs disk space.`, "Download the weights"))) return;
        b.disabled = true; cell.textContent = dl ? "downloading…" : "checking…";
        let html;
        try {
          const r = await api(`/api/embeddings/${encodeURIComponent(name)}/${dl ? "download" : "probe"}`, { method: "POST" });
          html = r.ok ? `<span class="ok">ok</span> dim ${esc(r.dim)}${r.elapsed_s != null ? ` · ${esc(r.elapsed_s)}s` : ""}${r.sim_related != null ? ` · related ${esc(r.sim_related)} vs unrelated ${esc(r.sim_unrelated)}` : ""}`
                      : `<span class="bad">${r.skipped ? "skipped" : "failed"}</span> ${esc(r.error || "")}`;
        } catch (e) { html = `<span class="bad">${esc(e.message)}</span>`; }
        embChecked = { name, html };
        await embeddingPicker(box, opts);   // the card's last check (and downloaded) line is redrawn from the server
      } else if (act === "edit") {
        openForm(Backends.embeddingFormValues(d.embeddings.find(x => x.name === name)));
      } else if (act === "remove") {
        if (!(await askConfirm(`Remove embedding ${name}?`, "Remove"))) return;
        await send(`/api/embeddings/${encodeURIComponent(name)}`, "DELETE");
        await again();
      }
    }, msg));
    if (embChecked) {
      const cell = box.querySelector(`[data-presult="${CSS.escape(embChecked.name)}"]`);
      if (cell) cell.innerHTML = embChecked.html;
    }
    return { rows: d.embeddings, chosen };
  }

  return { cardsHtml, wire, chipHtml, embeddingPicker, llmPicker, runLine, passPickersHtml, passFields };
})();
if (typeof module !== "undefined" && module.exports) module.exports = Pickers;
