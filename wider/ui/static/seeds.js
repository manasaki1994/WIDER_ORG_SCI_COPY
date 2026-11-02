// The "Names from seed papers" card on the Construct workbench (vision change 2b). Extract the seed PDFs' text, mine
// the names they use, add a name to a block (a new draft synonyms version, journalled), see the name map and the
// name-by-venue table and label a venue as a stream. Same engine as `wider seeds extract|mine|names|streams`
// (/api/seeds/*): the status lines ("retrieves B002", "not admitted: retrieves no development seed") come from it.
// A name is added only on the researcher's click; a save over a frozen query shows Refine's confirm step first.
(function () {
  let state = { names: null, streams: null };

  // A pure builder: one row per candidate with its status and, unless admitted, an "Add to block" button.
  function namesHtml(d) {
    if (!d || !d.candidates || !d.candidates.length) return `<p class="muted small">No candidate names yet: add a PDF per seed paper on the Review plan, then Extract and Mine.</p>`;
    const rows = d.candidates.map(c => {
      const also = (c.same_as || []).length ? ` <span class="muted small">also called ${esc(c.same_as.join(", "))}</span>` : "";
      const btn = c.admitted || (c.held || []).length ? "" : `<button class="secondary small-btn" data-admit="${esc(c.name)}" data-block="${esc(c.block || "")}">Add to block</button>`;
      const cls = c.admitted ? "ok" : (c.gains && c.gains.length ? "" : "muted");
      return `<tr><td>${esc(c.name)}${also}</td><td class="small">${esc(c.pattern)} ×${esc(c.count)}</td>
        <td class="small ${cls}" data-status="${esc(c.key)}">${esc(c.status || "no synonyms version to test against")}</td><td>${btn}</td></tr>`;
    }).join("");
    return `<table class="sn-names"><tr><th>name</th><th>found by</th><th>in the dry run</th><th></th></tr>${rows}</table>`;
  }

  // The name map in words: groups of names for one construct, then the look-alikes.
  function mapHtml(m) {
    const lines = [...(m && m.groups || []).filter(g => g.length > 1).map(g => `Same construct: ${g.join(", ")}`),
                   ...(m && m.look_alikes || []).map(x => `Look-alike: ${x.name} (a different construct from ${x.of})`)];
    return lines.length ? `<ul class="small sn-map">${lines.map(l => `<li>${esc(l)}</li>`).join("")}</ul>`
      : `<p class="muted small">No names grouped yet ("also called" sentences and your same-construct answers group them).</p>`;
  }

  // The name-by-venue table of the seeds' reference lists: a column per venue (its stream label below the name).
  function streamsHtml(t) {
    if (!t || !t.references) return `<p class="muted small">No parsed reference lists yet (Extract).</p>`;
    const head = t.venues.map(v => `<th data-venue="${esc(v.venue_norm)}">${esc(v.venue)}<br><span class="muted small">${
      v.label ? `stream: ${esc(v.label)}` : "no stream label"} · ${esc(v.references)} ${v.references === 1 ? "reference" : "references"}</span></th>`).join("");
    const rows = (t.names || []).map(n => `<tr><td>${esc(n.name)}</td>${t.venues.map(v => `<td>${esc(n.counts[v.venue_norm] || 0)}</td>`).join("")}</tr>`).join("")
      || `<tr><td class="muted small" colspan="${t.venues.length + 1}">no candidate or admitted name appears in a reference title</td></tr>`;
    return `<table class="sn-streams"><caption class="small">Names counted in the reference titles of each venue (counting only; you decide what a stream is)</caption><tr><th>name</th>${head}</tr>${rows}</table>`;
  }

  function msg(text) { $("#sn-msg").textContent = text; }

  function render() {
    const d = state.names || {};
    $("#sn-names").innerHTML = (d.notice ? `<p class="muted small">${esc(d.notice)}</p>` : "") + namesHtml(d);
    $("#sn-block").innerHTML = (d.blocks || []).map(b => `<option value="${esc(b)}">${esc(b)}</option>`).join("")
      || `<option value="">first block with a gain</option>`;
    $("#sn-map").innerHTML = mapHtml(d.map);
    const t = state.streams;
    $("#sn-streams").innerHTML = streamsHtml(t);
    $("#sn-venue").innerHTML = ((t && t.venues) || []).map(v => `<option value="${esc(v.venue_norm)}">${esc(v.venue)}</option>`).join("");
    $("#sn-names").querySelectorAll("[data-admit]").forEach(b => b.onclick = () => admit(b.dataset.admit, b.dataset.block));
  }

  async function load() {
    try {
      [state.names, state.streams] = await Promise.all([api("/api/seeds/names"), api("/api/seeds/streams")]);
    } catch (e) { msg(String(e.message || e)); return; }
    render();
  }

  // Add a name: the block chosen in the select (else the row's own block), the note field; Refine's confirm step
  // first when the save would reopen a frozen query ("Cancel" writes nothing).
  let admitting = false;   // a double click adds the name once (ui-tester F1)
  async function admit(name, rowBlock) {
    if (admitting) return;
    admitting = true;
    try { await admitOnce(name, rowBlock); } finally { admitting = false; }
  }

  async function admitOnce(name, rowBlock) {
    const block = $("#sn-block").value || rowBlock || null, note = $("#sn-note").value.trim() || null;
    // A name the dry run shows retrieving no seed in this block needs a note: ask for it before the confirm step.
    const c = ((state.names || {}).candidates || []).find(x => x.name === name);
    if (c && !note && (!block || block === c.block) && !(c.gains || []).length) {
      msg(`"${name}" retrieves no development seed in block ${block || "the first block"}: write a note to add it anyway. Nothing saved.`);
      $("#sn-note").focus();
      return;
    }
    const go = window.Refine ? await Refine.confirmSave("synonyms", `Add "${name}" to block ${block || ""}?`, "Add") : true;
    if (!go) { msg("Nothing saved."); return; }
    let r;
    try {
      const body = { name, block, note };
      if (typeof go === "string") body.seen_choice = go;
      r = await api("/api/seeds/names/admit", { method: "POST", body: JSON.stringify(body) });
    } catch (e) { msg(String(e.message || e)); return; }
    msg(`${r.notice ? r.notice + " " : ""}synonyms v${r.version} saved: ${r.note}${(r.closed_questions || []).length ? `; closed the open question(s) ${r.closed_questions.join(", ")} about this name` : ""}`);
    $("#sn-note").value = "";
    if (typeof loadConstruct === "function") await loadConstruct(); else await load();
  }

  async function step(url, body, done) {
    msg("Working…");
    try { const r = await api(url, { method: "POST", body: JSON.stringify(body) }); msg(done(r)); }
    catch (e) { msg(String(e.message || e)); return; }
    await load();
  }

  async function saveLabel() {
    const venue = $("#sn-venue").value, stream = $("#sn-stream").value.trim();
    if (!venue) { msg("No venue to label yet."); return; }
    await step("/api/seeds/streams/label", { venue, stream },
      r => r.label ? `${r.venue_norm}: stream "${r.label}"` : `${r.venue_norm}: label removed`);
    $("#sn-stream").value = "";
  }

  function wire() {
    $("#sn-extract").onclick = () => step("/api/seeds/extract", {}, r => `Extracted ${r.seeds.length} seed PDF(s) on this computer.`);
    $("#sn-mine").onclick = () => step("/api/seeds/mine", { model: true }, r => `${r.candidates.length} candidate names (${r.deterministic} from patterns)${r.model && r.model.message ? `. ${r.model.message}` : ""}.${r.validation_notice ? ` ${r.validation_notice}` : ""}${r.interview_notice ? ` ${r.interview_notice}.` : ""}`);
    $("#sn-mine-plain").onclick = () => step("/api/seeds/mine", { model: false }, r => `${r.candidates.length} candidate names from patterns (no model asked).${r.validation_notice ? ` ${r.validation_notice}` : ""}${r.interview_notice ? ` ${r.interview_notice}.` : ""}`);
    $("#sn-label-save").onclick = saveLabel;
  }

  // The Settings "Seed papers" card (`wider seeds policy`): the passages switch and what it lets out, the passage
  // size, and the backends allowed passages, each with "Withdraw" (`wider providers revoke NAME --for passages`).
  function policyHtml(s) {
    const b = s.backend;
    const here = b ? `The search-terms model ${esc(b.name)} (${esc(b.locality_label)}): ${esc(b.state)}.` : "";
    const allowed = (s.passages_allowed || []).map(n => `<li>${esc(n)} <button type="button" class="secondary small-btn" data-passages-revoke="${esc(n)}">Withdraw</button></li>`).join("");
    return `<h2>Seed papers</h2>
      <p>${s.passages_to_cloud ? '<span class="chip loc-cloud">on</span>' : '<span class="chip loc-local">off</span>'} ${esc(s.lets_out)}. The PDF itself never leaves this computer.</p>
      ${here ? `<p class="small">${here}</p>` : ""}
      <div class="row"><label><input type="checkbox" data-passages-switch ${s.passages_to_cloud ? "checked" : ""}> Send passages of seed PDFs to a model that is not on this computer</label></div>
      <div class="row"><label class="small">Passage size, tokens <input type="number" min="100" max="8000" step="100" value="${esc(s.passage_tokens)}" data-passage-tokens></label>
        <button type="button" class="secondary small-btn" data-passage-save>Save size</button> <span class="muted small" data-seed-policy-msg role="status"></span></div>
      <div class="row"><label class="small">GROBID server for seed reference lists (on this computer only; it gets the whole PDF)
        <input type="url" size="28" placeholder="http://127.0.0.1:8070" value="${esc(s.grobid_url || "")}" data-grobid-url></label>
        <button type="button" class="secondary small-btn" data-grobid-save>Save server</button>
        <span class="small" data-grobid-msg role="status"></span></div>
      ${allowed ? `<p class="small">Allowed for passages:</p><ul class="small">${allowed}</ul>` : '<p class="muted small">No model has been allowed passages.</p>'}
      ${cmdFold("<code>wider seeds policy passages-to-cloud on|off [--passage-tokens N]</code>, <code>wider seeds policy grobid-url URL|off</code>, <code>wider providers revoke NAME --for passages</code>")}`;
  }
  async function renderPolicy(el) {
    const card = typeof el === "string" ? document.querySelector(el) : el;
    if (!card) return;
    let s;
    try { s = await api("/api/seeds/policy"); }
    catch (e) { card.innerHTML = `<h2>Seed papers</h2><p class="bad">${esc(e.message)}</p>`; return; }
    card.innerHTML = policyHtml(s);
    const say = (t) => { const m = card.querySelector("[data-seed-policy-msg]"); if (m) m.textContent = t; };
    const after = async (t) => { await renderPolicy(card); say(t); };
    const post = async (body, t) => {
      try { await api("/api/seeds/policy", { method: "POST", body: JSON.stringify(body) }); await after(t); }
      catch (e) { say(e.message); }
    };
    const sw = card.querySelector("[data-passages-switch]");
    sw.onchange = () => post({ passages_to_cloud: sw.checked }, sw.checked ? "Passages on: a model that is not on this computer gets them once you allow it."
      : "Passages off: such a model is skipped and only the pattern pass runs.");
    card.querySelector("[data-passage-save]").onclick = () => post({ passage_tokens: Number(card.querySelector("[data-passage-tokens]").value) }, "Passage size saved.");
    card.querySelector("[data-grobid-save]").onclick = async () => {
      const url = card.querySelector("[data-grobid-url]").value.trim();
      try { await api("/api/seeds/policy", { method: "POST", body: JSON.stringify({ grobid_url: url || "off" }) }); await after(url ? "GROBID server saved." : "GROBID server cleared: the grobid tier is off."); }
      catch (e) { const m = card.querySelector("[data-grobid-msg]"); if (m) m.innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
    };
    card.querySelectorAll("[data-passages-revoke]").forEach(b => b.onclick = async () => {
      const name = b.dataset.passagesRevoke;
      try { await api(`/api/providers/${encodeURIComponent(name)}/consent?operation=passages`, { method: "DELETE" }); await after(`${name} is no longer allowed passages; the next model pass asks again.`); }
      catch (e) { say(e.message); }
    });
  }

  window.SeedNames = { load, wire, render, admit, saveLabel, namesHtml, mapHtml, streamsHtml, policyHtml, renderPolicy };
  if (typeof document !== "undefined" && document.getElementById) wire();
})();
