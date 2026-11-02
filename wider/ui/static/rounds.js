// The "Rounds" card on the Search strings page (D8): one round per synonyms version, with its recall from the last
// dry run, the record count (exported, or typed and marked pasted), the recall-versus-yield plot and its text table,
// and "Accept this round". Same engine as `wider query rounds | count | accept` (/api/query/rounds...). Accepting
// never refuses: a round that loses a seed shows the loss in a confirm step first; Cancel changes nothing.
// Also fills the upload card's round selector (#imp-round): every version, the frozen query preselected, else the newest.
(function () {
  const lines = (arr) => (arr || []).map(t => `<p>${esc(t)}</p>`).join("");

  // A pure builder: the rounds table (one row per round and platform), with an accept button and count fields.
  function listHtml(d) {
    if (!d.rounds || !d.rounds.length) return `<p class="muted small">No rounds yet: save a synonyms version first.</p>`;
    const plats = d.count_platforms || [];
    const rows = d.rounds.map(r => {
      const ps = r.platforms.length ? r.platforms : [{ platform: "", recall_text: "no dry run yet", count_text: d.no_export || "no export yet" }];
      const head = `v${esc(r.round)} <span class="muted small">(${esc(r.status)}${r.frozen_query ? ", the frozen query" : ""})</span>`;
      const tone = !r.accepted ? "muted" : (r.lost && r.lost.length ? "warn-text" : "ok");   // a loss is not shown as good news
      const mark = r.mark ? `<span class="${tone} small" data-mark="${esc(r.round)}">${esc(r.mark)}</span>` : "";
      const acc = (r.accepted ? "" : `<button type="button" class="secondary small-btn" data-accept="${esc(r.round)}">Accept this round</button>`)
        + ` <button type="button" class="secondary small-btn" data-replay="${esc(r.round)}" title="Recompute offline and compare with the round's record">Replay</button>`
        + ` <button type="button" class="secondary small-btn" data-prov="${esc(r.round)}">Provenance</button>`;
      const counts = plats.map(k => `<label class="small">${esc(k)} <input type="text" inputmode="numeric" size="7" data-count-round="${esc(r.round)}" data-count-platform="${esc(k)}" aria-label="Records ${esc(k)} showed for v${esc(r.round)}"></label>`).join(" ");
      const form = plats.length ? `<details class="small"><summary>Type a count</summary>${counts} <button type="button" class="secondary small-btn" data-count-save="${esc(r.round)}">Save counts</button></details>` : "";
      return ps.map((p, i) => `<tr data-round="${esc(r.round)}">${i === 0 ? `<td rowspan="${ps.length}">${head}<br>${mark} ${acc}${form}</td>` : ""}
        <td>${esc(p.platform)}</td><td>${esc(p.recall_text)}${p.stale ? ' <span class="muted small">(seeds changed since this dry run)</span>' : ""}</td>
        <td${p.count_source === "pasted" ? ' class="pasted"' : ""}>${esc(p.count_text)}</td></tr>`).join("");
    }).join("");
    return `<table class="rd-table"><tr><th>round</th><th>platform</th><th>development recall</th><th>records</th></tr>${rows}</table>`;
  }

  // The plot is built by the server from numbers only (escaped there); the text table holds the same numbers.
  function plotHtml(d) {
    return `<figure class="rd-plot">${d.svg || ""}<figcaption class="small"><ul>${(d.table || []).map(t => `<li>${esc(t)}</li>`).join("")}</ul></figcaption></figure>`;
  }

  // A pure builder: the confirm step before accepting a round that loses seeds (the lines the CLI prints after).
  function acceptHtml(round, plan) {
    return `<h2 id="choice-title">Accept v${esc(round)}?</h2>${lines(plan.lines)}
      <p class="muted small">The loss is recorded with the round. Undo it in History.</p>
      <div class="row"><button type="button" data-act="accept">Accept this round</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }
  // Whether accepting needs the confirm step: only a loss is confirmed; anything else is said after.
  const needsConfirm = (plan) => !!(plan && plan.loss_lines && plan.loss_lines.length);

  // A pure builder: the upload card's selector options.
  function selectorHtml(sel) {
    return (sel.options || []).map(o => `<option value="${esc(o.version)}"${o.version === sel.preselect ? " selected" : ""}>${esc(o.label)}</option>`).join("")
      || `<option value="">no synonyms version yet</option>`;
  }

  const msg = (t) => { const m = $("#rd-msg"); if (m) m.textContent = t; };

  async function accept(n) {
    try {
      const plan = await api(`/api/query/rounds/${n}/accept`);
      if (plan.already) { msg(plan.lines.join(" ")); return null; }
      if (needsConfirm(plan)) {
        const r = await askChoice(acceptHtml(n, plan));
        if (!r || r.act !== "accept") { msg("Nothing was accepted."); return null; }
      }
      const res = await api(`/api/query/rounds/${n}/accept`, { method: "POST" });
      await load();
      // the loss once, grouped by platform (the confirm step showed it per platform); then the other lines
      const rest = (res.lines || []).filter(t => !(res.loss_lines || []).includes(t));
      msg([`v${n} is now the accepted round${res.loss_text ? `; it loses ${res.loss_text}` : ""}.`].concat(rest).join(" "));
      return res;
    } catch (e) { msg(e.message); return null; }
  }

  // A pure builder: the Provenance card's body for one round (`wider query provenance`).
  function provHtml(v) {
    const short = (h) => h ? `<code>${esc(String(h).slice(0, 12))}</code>` : "";
    const dec = v.decisions.length
      ? `<ul>${v.decisions.map(d => `<li>#${esc(d.decision_id)} ${esc(d.text)}${d.dismissed ? ' <span class="muted">(dismissed)</span>' : ""}</li>`).join("")}</ul>`
      : `<p class="muted small">${esc(v.decisions_message)}</p>`;
    const calls = v.model_calls.length
      ? `<table class="rd-table"><tr><th>stage</th><th>run</th><th>model</th><th>input hash</th><th>output hash</th></tr>${v.model_calls.map(c => `<tr><td>${esc(c.stage)}</td><td>${esc(c.run_ref)}</td><td>${esc(c.provider)}${c.model ? ` <span class="muted">(${esc(c.model)})</span>` : ""}</td><td>${short(c.input_sha256) || "not recorded"}</td><td${c.failed ? " data-failed" : ""}>${c.output_sha256 && !c.failed ? short(c.output_sha256) : esc(c.note || "output hash not recorded")}</td></tr>`).join("")}</table>`
      : `<p class="muted small">no model calls shaped this round</p>`;
    const web = v.web_calls.length
      ? `<ul>${v.web_calls.map(w => `<li>${esc(fmtWhen(w.at))} ${esc(w.service)} ${esc(w.purpose)}: ${esc(w.items)} item(s), allowed by the ${esc(w.switch)}</li>`).join("")}</ul>`
      : `<p class="muted small">no outside requests in this round</p>`;
    return `<h3>Round v${esc(v.round)} <span class="muted small">from ${esc(v.window.from)} to ${esc(v.window.to || "now")}</span></h3>
      <h4>Decisions</h4>${dec}<h4>Model calls (${v.model_calls.length})</h4>${calls}<h4>Outside requests (${v.web_calls.length})</h4>${web}`;
  }

  async function provenance(n) {
    const out = $("#prov-out"), m = $("#prov-msg");
    if (m) m.textContent = "loading…";
    try {
      const v = await api(`/api/query/rounds/${n}/provenance`);
      if (out) out.innerHTML = provHtml(v);
      if (m) m.textContent = "";
      return v;
    } catch (e) { if (m) m.textContent = e.message; return null; }
  }

  // Replay: recompute the round offline and compare with its record (`wider query replay`); writes nothing.
  async function replay(n) {
    msg("replaying…");
    try { const r = await api(`/api/query/rounds/${n}/replay`); msg(`v${n}: ${r.message}`); return r; }
    catch (e) { msg(e.message); return null; }
  }

  async function saveCounts(n) {
    const said = [];
    for (const inp of document.querySelectorAll(`[data-count-round="${n}"]`)) {
      const v = inp.value.trim(); if (!v) continue;
      try {
        const r = await api(`/api/query/rounds/${n}/count`, { method: "POST", body: JSON.stringify({ platform: inp.dataset.countPlatform, n: v }) });
        said.push(r.line);
      } catch (e) { said.push(e.message); }
    }
    await load();
    msg(said.join(" ") || "Type a number first.");
  }

  async function load() {
    const out = $("#rd-out"); if (!out) return;
    try {
      const d = await api("/api/query/rounds");
      out.innerHTML = plotHtml(d) + listHtml(d);
      out.querySelectorAll("[data-accept]").forEach(b => { b.onclick = () => accept(Number(b.dataset.accept)); });
      out.querySelectorAll("[data-replay]").forEach(b => { b.onclick = () => replay(Number(b.dataset.replay)); });
      out.querySelectorAll("[data-prov]").forEach(b => { b.onclick = () => provenance(Number(b.dataset.prov)); });
      out.querySelectorAll("[data-count-save]").forEach(b => { b.onclick = () => saveCounts(Number(b.dataset.countSave)); });
      fill(d.selector);
    } catch (e) { msg(e.message); }
  }

  function fill(sel) {
    const s = $("#imp-round"); if (!s || !sel) return;
    s.innerHTML = selectorHtml(sel);
  }
  async function fillSelector() {
    try { fill((await api("/api/query/rounds")).selector); } catch (e) { /* the import still runs with the default */ }
  }
  // The query string the import sends ("" without a version: the server takes the frozen query, else the newest).
  function roundParam() {
    const s = $("#imp-round");
    return s && s.value ? `round=${encodeURIComponent(s.value)}` : "";
  }

  const b = $("#rd-refresh");
  if (b) b.onclick = async () => {
    msg("running…");
    try { const r = await api("/api/query/rounds/refresh", { method: "POST" }); await load(); msg(`Dry-ran ${r.refreshed.length} round(s) on today's seeds.`); }
    catch (e) { msg(e.message); }
  };
  globalThis.Rounds = { load, accept, replay, provenance, provHtml, saveCounts, listHtml, plotHtml, acceptHtml, needsConfirm, selectorHtml, fillSelector, roundParam };
})();
