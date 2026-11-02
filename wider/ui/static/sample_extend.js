// "Add boundary cases" on the card of an unlocked purposive sample (Sampling page; change screening-config-comparison,
// D8, D9). The button posts to /api/samples/{name}/extend, which calls the engine `wider sample extend` calls; the
// answer is shown in the CLI's own words (records added per pool, then the engine's messages) in normal text, red when
// the server refuses. The card also gives the boundary line of `wider sample status`. Builders return HTML strings
// (every value through `esc`, common.js); `click` is given its api and a reload, so tests run it under Node with fakes.
const SampleExtend = (() => {
  const msgs = {};       // sample name -> { lines, bad }: the last answer, kept across the card's redraw
  const busy = new Set();
  const canExtend = (s) => !!s && s.kind === "purposive" && !s.locked;
  const poolText = (pools) => Object.entries(pools || {}).map(([k, v]) => `${k} ${v}`).join(", ");

  // the boundary line of `wider sample status`: the draw's pools plus every extension's, else the draw's reason
  // (wider/sample/design.py boundary_line)
  function boundaryText(s) {
    const ext = (s && s.extensions) || [];
    if (!s || s.kind !== "purposive" || (!("boundary" in s) && !ext.length)) return "";
    const b = s.boundary, pools = {};
    if (b && typeof b === "object") Object.assign(pools, b.pools || {});
    for (const e of ext) for (const [k, v] of Object.entries(e.pools || {})) pools[k] = (pools[k] || 0) + v;
    return `boundary: ${poolText(pools) || (typeof b === "string" ? b : "none drawn")}`;
  }
  // the text output of `wider sample extend` (cli.py sample_extend)
  function resultLines(name, r) {
    const lines = r.added ? [`${name}: ${r.added} record(s) added, now ${r.n}: ${poolText(r.pools)}`] : [];
    return lines.concat(r.messages || []);
  }
  function cardHtml(name, s) {
    const line = boundaryText(s), m = msgs[name];
    const btn = canExtend(s) ? `<button type="button" class="secondary" data-extend="${esc(name)}"${busy.has(name) ? " disabled" : ""}>Add boundary cases</button>` : "";
    const said = m ? m.lines.map((l) => esc(l)).join("<br>") : "";   // every line escaped, one per row
    const msg = said ? `<p data-extend-msg role="status"${m.bad ? ' class="bad"' : ""}>${said}</p>` : "";
    if (!line && !btn && !msg) return "";
    return `${line ? `<p data-boundary>${esc(line)}</p>` : ""}${btn ? `<div class="row">${btn}</div><p class="send-note" data-send="embed_short" hidden></p>` : ""}${msg}`;
  }
  // post the extension; the answer (or the refusal) is kept for the card, then the page reloads the samples. After a
  // corpus round the server first answers 409 with `confirm_needed` and the sentence on what the added records hide
  // until labelled (ui-redesign D4): it is asked with deps.askConfirm, and only "Add them" posts again with confirm
  async function click(name, deps) {
    if (busy.has(name)) return null;
    busy.add(name);
    let r = null;
    const post = (extra) => deps.api(`/api/samples/${encodeURIComponent(name)}/extend`, { method: "POST", body: JSON.stringify({ pool: "boundary", ...extra }) });
    try {
      try { r = await post({}); }
      catch (e) {
        if (!(e.status === 409 && e.body && e.body.confirm_needed && deps.askConfirm)) throw e;
        if (!(await deps.askConfirm(e.body.detail, "Add them"))) { msgs[name] = { lines: ["Nothing was changed."], bad: false }; }
        else r = await post({ confirm: true });
      }
      if (r) msgs[name] = { lines: resultLines(name, r), bad: false };
    } catch (e) { msgs[name] = { lines: [e.message], bad: true }; }
    finally { busy.delete(name); }
    if (deps.reload) await deps.reload(r);
    return r;
  }
  // "Add records to bands" on an unlocked probability sample (change validation-sample-planner D6): a number field
  // per band, empty by default; it posts to /api/validation/extend (CLI twin: wider validation extend NAME --band B=M)
  const canAddBands = (s) => !!s && s.kind === "probability" && !s.locked && Array.isArray(s.bands) && s.bands.length > 0;
  function bandsHtml(name, s) {
    if (!canAddBands(s)) return "";
    const m = msgs[name];
    const fields = s.bands.map(b => `<label>${esc(b.band)} <input type="number" min="0" step="1" data-add-band="${esc(b.band)}" data-add-sample="${esc(name)}"></label>`).join(" ");
    const said = m ? m.lines.map((l) => esc(l)).join("<br>") : "";   // every line escaped, one per row
    const msg = said ? `<p data-extend-msg role="status"${m.bad ? ' class="bad"' : ""}>${said}</p>` : "";
    return `<div class="row">${fields}<button type="button" class="secondary" data-add-bands="${esc(name)}"${busy.has(name) ? " disabled" : ""}>Add records to bands</button></div>${msg}`;
  }
  // the route's body from the fields: only the bands given a positive whole number
  function bandsBody(name, values) {
    const bands = {};
    for (const [b, v] of Object.entries(values || {})) { const n = Number(String(v ?? "").trim()); if (String(v ?? "").trim() !== "" && Number.isInteger(n) && n > 0) bands[b] = n; }
    return { sample: name, bands };
  }
  async function addBands(name, values, deps) {
    if (busy.has(name)) return null;
    const body = bandsBody(name, values);
    if (!Object.keys(body.bands).length) { msgs[name] = { lines: ["Give a number of records for at least one band."], bad: true }; if (deps.reload) await deps.reload(null); return null; }
    busy.add(name);
    let r = null;
    try {
      r = await deps.api("/api/validation/extend", { method: "POST", body: JSON.stringify(body) });
      msgs[name] = { lines: r.lines || [], bad: false };
    } catch (e) { msgs[name] = { lines: [e.message], bad: true }; }
    finally { busy.delete(name); }
    if (deps.reload) await deps.reload(r);
    return r;
  }
  const message = (name) => msgs[name] || null;
  // "Fresh sample instead of the used one" on the Draw card (D9): the used sample from a select, the new name typed
  // (suggested as the next free name), sent as POST /api/samples/draw?which=probability&name=..&fresh_from=..
  function freshName(used, names) {
    const base = String(used).replace(/\d+$/, "");
    let i = 2;
    while ((names || []).includes(`${base}${i}`)) i++;
    return `${base}${i}`;
  }
  function freshHtml(samples) {
    const prob = Object.entries(samples || {}).filter(([, s]) => s.kind === "probability").map(([k]) => k);
    if (!prob.length) return "";
    const opts = prob.map(k => `<option value="${esc(k)}"${samples[k].in_use ? " selected" : ""}>${esc(k)}</option>`).join("");
    const used = prob.find(k => samples[k].in_use) || prob[0];
    const name = freshName(used, Object.keys(samples));
    return `<div class="row"><label>Used sample <select data-fresh-from>${opts}</select></label> `
      + `<label>New name <input data-fresh-name value="${esc(name)}"></label> `
      + `<button type="button" class="secondary" data-fresh>Fresh sample instead of the used one</button></div>`
      + `<p class="send-note" data-send="embed_short" hidden></p>`
      + `<p>The fresh sample leaves out the used sample, the samples it replaced and the development records; recall then reads it.</p>`;
  }
  function freshUrl(used, name) {
    const q = new URLSearchParams({ which: "probability", name: String(name || "").trim(), fresh_from: used });
    return `/api/samples/draw?${q}`;
  }
  // the criteria counts of a probability sample (D11), twin of `wider sample status --json`; normal text, never grey
  function criteriaHtml(c) {
    if (!c || !c.lines || !c.lines.length) return "";
    return `<div data-criteria-counts>${c.lines.map(l => `<p>${esc(l)}</p>`).join("")}</div>`;
  }
  // the number of corrected labels on any sample (label-completion-and-sealing D2), said once; no decision or reason,
  // as a blind second reviewer may open the page; `wider label revisions` lists them
  function correctionsHtml(name, n) {
    if (!n) return "";
    const fold = typeof cmdFold === "function" ? cmdFold(`<code>wider label revisions ${esc(name)}</code>`) : "";   // common.js
    return `<p data-corrections>${esc(n)} corrected label${n === 1 ? "" : "s"}</p>${fold}`;
  }
  // labels on a probability sample whose independence is not stated (label-completion-and-sealing D9, D11): one line
  // per reviewer and "Declare labels independent", twin of `wider label provenance NAME --reviewer R --independent B`
  function independenceHtml(name, s) {
    const u = s && s.kind === "probability" && s.provenance && s.provenance.undeclared;
    if (!u || !Object.keys(u).length) return "";
    return `<div data-independence="${esc(name)}">`
      + `${Object.entries(u).map(([who, n]) => `<p>${esc(who)}: ${esc(n)} label${n === 1 ? "" : "s"} without an established independence</p>`).join("")}`
      + `<p>WIDER cannot tell whether these labels were made without seeing the AI model's decisions. If they were, say how, and they count as independent labels for the recall test.</p>`
      + `<button type="button" class="secondary" data-declare-independent="${esc(name)}">Declare labels independent</button></div>`;
  }
  // the dialog and POST /api/labels/independence; Cancel or an empty field saves nothing
  async function declareIndependent(name, s, deps) {
    const who = Object.keys((s && s.provenance && s.provenance.undeclared) || {});
    const f = await deps.askFields("Declare labels independent", [
      { key: "reviewer", label: "Reviewer", value: who.length === 1 ? who[0] : "" },
      { key: "basis", label: "Basis: how these labels were made without seeing model output" }], "Declare");
    const reviewer = f ? String(f.reviewer || "").trim() : "", basis = f ? String(f.basis || "").trim() : "";
    if (!reviewer || !basis) { deps.say(f ? "nothing was saved: a declaration needs a reviewer and a basis" : "nothing was saved"); return null; }
    try {
      const r = await deps.api("/api/labels/independence", { method: "POST", body: JSON.stringify({ sample: name, reviewer, basis }) });
      deps.say((r.lines || []).join(" "));
      if (deps.reload) await deps.reload();
      return r;
    } catch (e) { deps.say(String(e.message || e)); return null; }
  }
  // "Criteria changed after labelling" (D11): the difference and the recommendation said once, the scope as radio
  // buttons with none pre-selected, a note, and the "Revised labels" list (twin of `wider label revisions`)
  function criteriaChangeHtml(name, r, revs) {
    if (!r || !r.early || r.to_version == null || !r.recommendation) return "";
    const opts = [["none", "Not substantive: keep the labels"], ["affected", "Re-check the affected labels"],
                  ["all", "Re-check all early labels"]];
    const radios = opts.map(([v, t]) => `<label><input type="radio" name="crit-${esc(name)}" value="${v}" data-crit-scope> ${esc(t)}</label>`).join(" ");
    const revList = (revs || []).length
      ? `<h4>Revised labels</h4><ul>${revs.map(v => `<li>record ${esc(v.record_id)} (${esc(v.reviewer)}): ${esc(v.old_decision)} under v${esc(v.old_codebook_version)} to ${esc(v.new_decision)} under v${esc(v.new_codebook_version)}</li>`).join("")}</ul>` : "";
    return `<div data-criteria-change="${esc(name)}"><h4>Criteria changed after labelling</h4>`
      + (r.lines || []).map(l => `<p>${esc(l)}</p>`).join("")
      + `<fieldset><legend>Your assessment</legend>${radios}<br><label>Note <input type="text" data-crit-note></label> `
      + `<button type="button" class="secondary" data-crit-save="${esc(name)}">Record the assessment</button></fieldset>${revList}</div>`;
  }
  // the POST /api/labels/criteria body from the chosen scope (null when none is chosen)
  function criteriaBody(name, scope, note) {
    if (!scope) return null;
    return { sample: name, substantive: scope === "none" ? "no" : "yes", scope: scope === "none" ? null : scope,
             note: String(note || "").trim() || null };
  }
  return { criteriaChangeHtml, criteriaBody, canExtend, boundaryText, resultLines, cardHtml, click, message, canAddBands, bandsHtml, bandsBody, addBands,
           freshName, freshHtml, freshUrl, criteriaHtml, correctionsHtml, independenceHtml, declareIndependent };
})();
if (typeof module !== "undefined" && module.exports) module.exports = SampleExtend;
