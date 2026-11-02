// Record work (WP13): page tables that open the record drawer (drawer.js) for labelling, adjudication, dedup pairs,
// screening review, development evaluation and the notes-and-tags search on the home page.
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const h = esc;
  const call = api;
  const words = (s) => String(s || "").toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (hay, terms) => { const t = hay.toLowerCase(); return terms.every(w => t.includes(w)); };
  const tagChips = (a) => (a && a.tags && a.tags.length) ? a.tags.map(t => `<span class="chip tag-chip">${h(t)}</span>`).join(" ") : "";
  const noteMark = (a) => a && a.n_notes ? ` <span class="muted small" title="${a.n_notes} note${a.n_notes === 1 ? "" : "s"}">✎${a.n_notes}</span>` : "";
  const decClass = (d) => d === "include" ? "ok" : d === "exclude" ? "bad" : "";
  // blind mode needs a name: without one there is nothing of the reader's own to show, so nothing is asked
  const summary = (author, blind) => blind && !author ? Promise.resolve({}) : call(`/api/annotations/summary?author=${encodeURIComponent(author || "")}&blind=${blind ? "true" : "false"}`).catch(() => ({}));

  // =============================================================== labelling queue (blind)
  let lb = { rows: [], view: [], ann: {}, owner: "", mode: "queue", blind: true };   // owner: the name the shown queue was loaded for
  let lbExclAsked = false;   // the exclusion reason was shown by an Exclude that saved nothing yet
  const reviewer = () => WiderName.get();   // the one name WIDER works as (D33); the page has no field of its own
  // the queue counts as open while it is shown with records on the Samples and labelling page (common.js holds the
  // stage refresh meanwhile)
  window.queueOpen = () => typeof currentPage !== "undefined" && currentPage === "sample" && lb.rows.length > 0;
  // the re-check and second-reviewer queues (D12) hold opaque items {item, title, abstract}: no sample, no record id,
  // so the drawer shows no notes, tags or History panel and a save posts the item alone
  const lbMode = () => (q("#lb-mode") && q("#lb-mode").value) || "queue";
  // the plain queue can be narrowed to one sample, a locked one too (missing labels save at once, a change asks for a
  // correction reason), and unblinded with the switch
  const lbSample = () => (q("#lb-sample") && q("#lb-sample").value) || "";
  const lbBlind = () => !(q("#lb-blind") && q("#lb-blind").checked === false);
  const queueUrl = (mode, rev) => mode === "queue" ? `/api/labels/queue?reviewer=${encodeURIComponent(rev)}` +
      (lbSample() ? `&samples=${encodeURIComponent(lbSample())}` : "") + (lbBlind() ? "" : "&blind=false")
    : `/api/labels/queue?mode=${mode}&reviewer=${encodeURIComponent(rev)}`;
  const leftOutLine = (n) => `${n} record${n === 1 ? "" : "s"} not labelled yet ${n === 1 ? "is" : "are"} in a queue of their own: ` +
    "choose a sample in the sample select";
  // a change to a saved label on a locked sample or a sealed record needs a reason (label-completion-and-sealing D9):
  // the route answers 409 with correction_needed and its sentence (blind, it names no sample); the dialog asks for the
  // reason and the same request goes again with it; Cancel or an empty reason saves nothing
  async function postCorrectable(path, payload) {
    try { return await call(path, { method: "POST", body: JSON.stringify(payload) }); }
    catch (e) {
      if (e.status !== 409 || !(e.body && e.body.correction_needed)) throw e;
      // the queue narrowed to one sample names it in the title: the reviewer chose it, so nothing is unblinded (M17)
      const f = await askFields(`Reason for this correction${lbSample() ? ` (sample ${lbSample()})` : ""}`, [{ key: "why", label: e.message }], "Save correction");
      const why = f ? String(f.why || "").trim() : "";
      if (!why) throw new Error(f ? "nothing was saved: a correction needs a reason" : "nothing was saved");
      return call(path, { method: "POST", body: JSON.stringify({ ...payload, correction_reason: why }) });
    }
  }
  // the sample select, filled by the Samples page's loadSamples (app.js); a locked sample is marked so
  window.lbSampleOptions = (samples) => {
    const sel = q("#lb-sample"); if (!sel) return;
    const keep = sel.value;
    sel.innerHTML = '<option value="">all samples</option>' + Object.entries(samples || {}).map(([n, s]) =>
      `<option value="${h(n)}"${n === keep ? " selected" : ""}>${h(n)}${s.locked ? " (locked)" : ""}</option>`).join("");
  };

  // who signs (D10): the name WIDER works as, in normal text; once, the command line's name when it differs
  let envReviewer = "";
  window.lbSigning = (env) => {
    if (env !== undefined) envReviewer = String(env || "").trim();
    const el = q("#lb-signing"); if (!el) return;
    const who = reviewer();
    el.innerHTML = !who ? `no name yet: labels need your name. <button type="button" class="secondary small" data-set-name>Set your name</button>`
      : `Signing as <b>${h(who)}</b>.` + (envReviewer && envReviewer !== who ? ` The command line signs as <b>${h(envReviewer)}</b>.` : "");
  };

  // the fields saved with a decision; on the focus page they sit under the decision buttons (.dw-after), the exclusion
  // reason only once Exclude is chosen or saved (label-focus-pages D8)
  const codebook = () => (typeof lbCodebook !== "undefined" && lbCodebook) || {};
  // a codebook value is saved as written (senior_management); the page shows it in words (owner inspection)
  const readable = (v) => String(v == null ? "" : v).replace(/_/g, " ");
  function lbControls(item) {
    const cb = codebook();
    const reasons = cb.exclusion_reasons || [], groups = cb.labels || [];
    const labs = item.labels ? (typeof item.labels === "string" ? JSON.parse(item.labels || "{}") : item.labels) : {};
    return `<div class="dw-controls dw-after" id="dw-controls">
      <label class="small" id="dw-reason-row"${reasons.length && item.decision !== "exclude" ? " hidden" : ""}>Exclusion reason <select id="dw-reason" aria-describedby="dw-dec-msg"><option value="">none</option>${reasons.map(r => `<option value="${h(r.code)}"${item.reason === r.code ? " selected" : ""}>${h(r.code)} ${h(r.text)}</option>`).join("")}</select></label>
      ${groups.map(g => `<label class="small">${h(readable(g.group))} <select data-label="${h(g.group)}"><option value="">–</option>${g.values.map(v => `<option value="${h(v)}"${labs[g.group] === v ? " selected" : ""}>${h(readable(v))}</option>`).join("")}</select></label>`).join("")}
      <label class="small fx-wide">Note saved with your label (optional) <input id="dw-labnote" value="${h(item.note || "")}" autocomplete="off"></label></div>`;
  }

  // the criteria beside the record on the focus pages: the codebook's elements and exclusion reasons
  function criteria(title) {
    const cb = codebook(), els = cb.elements || [], reasons = cb.exclusion_reasons || [];
    if (!els.length && !reasons.length) return "";
    const ver = typeof lbCodebookVersion !== "undefined" && lbCodebookVersion != null ? `<p class="fx-cver">Codebook version ${h(lbCodebookVersion)}</p>` : "";
    return `<h2>${h(title)}</h2>`
      + (els.length ? `<p class="fx-inc">Include when</p><ul>${els.map(e => `<li>${h(e.name || e.id)}${e.rule ? `: ${h(e.rule)}` : ""}</li>`).join("")}</ul>` : "")
      + (reasons.length ? `<p class="fx-exc">Exclude when</p><ul>${reasons.map(r => `<li><b>${h(r.code)}</b> ${h(r.text)}</li>`).join("")}</ul>` : "") + ver;
  }
  const EXCL_ASK = "Choose an exclusion reason: picking one (a click, or Enter on the list) saves the exclude and moves on. Or press Exclude again to save it without a reason.";
  // what a decision is saved with: the same decision pressed again with the same fields sends nothing (drawer sameAs)
  const lbFields = (body) => {
    const box = (window.Focus && Focus.isOpen() && q("#fx-after")) || body;
    const v = (sel) => { const x = box && q(sel, box); return x ? x.value : ""; };
    const labs = box && box.querySelectorAll ? [...box.querySelectorAll("select[data-label]")].map(x => x.value) : [];
    return JSON.stringify([v("#dw-reason"), labs, v("#dw-labnote")]);
  };

  function lbRender() {
    const f = q("#lb-filter").value, terms = words(q("#lb-search").value);
    lb.view = lb.rows.filter(r => (f === "all" || (f === "labelled" ? !!r.decision : !r.decision)) &&
      matches(`${r.title || ""} ${(lb.ann[r.record_id] || {}).text || ""} ${((lb.ann[r.record_id] || {}).tags || []).join(" ")}`, terms));
    const done = lb.rows.filter(r => r.decision).length;
    q("#lb-progress").textContent = lb.rows.length ? `${done} of ${lb.rows.length} labelled · showing ${lb.view.length}` : "queue empty";
    const t = q("#lb-table");
    t.innerHTML = lb.view.length ? `<thead><tr><th>#</th><th>title</th><th>your decision</th><th>tags</th></tr></thead><tbody>` +
      lb.view.map((r, i) => `<tr data-idx="${i}"><td class="muted">${i + 1}</td><td>${h(r.title || "(no title)")}${noteMark(lb.ann[r.record_id])}${lb.blind ? "" : unblindedBits(r)}</td>
        <td class="${decClass(r.decision)}">${h(r.decision || "")}${r.reason ? ` <span class="muted small">${h(r.reason)}</span>` : ""}</td><td>${tagChips(lb.ann[r.record_id])}</td></tr>`).join("") + "</tbody>" : "";
    Drawer.bindRows(t, (i) => lbOpen(i));
  }

  // only with the blind switch off: where the record was drawn from, as an unblinded queue shows it
  const unblindedBits = (r) => ` <span class="muted small">${[r.sample, r.band, r.rank != null ? `rank ${r.rank}` : ""].filter(Boolean).map(h).join(" · ")}</span>`;

  async function lbLoad(openFirst) {
    const rev = reviewer();
    if (!rev) { WiderName.ask(); return; }   // "Open queue" without a name opens the name dialog, nothing is requested
    const mode = lbMode();
    q("#lb-lines").innerHTML = "";
    try {
      if (mode === "queue") {
        // the count of unlabelled records a mixed blind queue left out comes in a header and names no sample (D8);
        // a stand-in for the route may answer with the list alone
        const [res, ann] = await Promise.all([call(queueUrl(mode, rev), { withHeaders: true }), summary(rev, true)]);
        const n = Array.isArray(res) || !res.headers ? 0 : Number(res.headers.get("X-Left-Out") || 0);
        lb.rows = Array.isArray(res) ? res : res.body; lb.ann = ann;
        if (n > 0) q("#lb-lines").innerHTML = `<p>${h(leftOutLine(n))}</p>`;
      } else {
        const res = await call(queueUrl(mode, rev));
        lb.rows = res.items; lb.ann = {};
        q("#lb-lines").innerHTML = res.lines.map(l => `<p>${h(l)}</p>`).join("");
      }
    } catch (e) { q("#lb-progress").textContent = e.message; return; }
    lb.owner = rev; lb.mode = mode; lb.blind = mode !== "queue" || lbBlind();
    lbRender();
    if (window.askBoxRedraw) window.askBoxRedraw();   // a queue with records leaves Ask WIDER out of the sidebar, top bar and guide (guide-assistant)
    if (openFirst && lb.view.length) lbOpen(0);
  }

  function lbOpen(i) {
    const owner = lb.owner;   // decisions and notes are signed with the name the queue was loaded for
    const byItem = lb.mode !== "queue";
    const labelled = () => lb.rows.filter(x => x.decision).length;
    const pr = q("#lb-progress"); if (pr && pr.setAttribute) pr.setAttribute("aria-live", "off");   // read again on close
    (window.Focus || Drawer).open({   // focus.js; a page without it opens the side drawer
      full: { title: "Label records", leave: "Leave labelling", barLabel: "Labelled",
        signing: `signing as <b>${h(owner)}</b>`,
        progress: (r) => ({ text: `Record ${lb.view.indexOf(r) + 1} of ${lb.view.length} · you have labelled ${labelled()} of ${lb.rows.length}`, done: labelled(), total: lb.rows.length }),
        note: lb.blind ? "You see only the record and your criteria. The AI model's decision, the other reviewer's label, the rank, the score and the sample stay hidden." : "",
        aside: () => criteria("Your criteria") },
      label: "Labelling queue (blind)",
      ...(byItem ? { label: lb.mode === "recheck" ? "Re-check queue (blind)" : "Second reviewer queue (blind)" } : {}),
      table: "#lb-table", items: lb.view, index: i, blind: true, author: owner,
      historyNote: byItem ? "earlier labels are not shown in this queue" : "",
      recordId: (r) => byItem ? null : r.record_id,
      render: async (r, u) => u.recordBlock(r, []) + lbControls(r),
      // after the first Exclude, a reason picked with the mouse or with Enter on the list saves the exclude with it and
      // moves on, once. An arrow key or a typed letter on a closed list also fires "change" at each step, so a change
      // that soon after such a key only moves the choice (risk-checker R1). Enter waits a moment: a list opened on a
      // Mac takes its new value only after the key, and saving at once lost the reason (owner fixes 1 Nov 2026).
      afterRender: () => {
        lbExclAsked = false; const s = q("#dw-reason"); if (!s) return;
        let stepAt = 0;
        // once: Enter on an open list also fires "change", and a second click would be refused as an ignored key press
        const save = () => { const b = q('#dw-decide [data-dec="1"]'); if (lbExclAsked && b) { lbExclAsked = false; b.click(); } };
        s.onchange = () => { if (Date.now() - stepAt > 300) save(); };
        s.onkeydown = (e) => {
          if (e.key === "Enter") { e.preventDefault(); setTimeout(save, 0); }
          else if (e.key.length === 1 || /^(Arrow|Home|End|Page)/.test(e.key)) stepAt = Date.now();
        };
      },
      sameAs: (r, body) => lbFields(body),
      current: (r) => r.decision,
      decisions: ["include", "exclude", "maybe"].map((d, i) => ({ value: d, label: d[0].toUpperCase() + d.slice(1), tone: d, fn: async (r, body) => {
        const box = (window.Focus && Focus.isOpen() && q("#fx-after")) || body;   // under the decision buttons on the focus page
        const row = q("#dw-reason-row");
        // a first Exclude shows the exclusion reason and asks once; Exclude again saves without one (inform, not control)
        if (d === "exclude" && row && row.hidden) { row.hidden = false; lbExclAsked = true; const s = q("#dw-reason"); if (s && s.focus) s.focus(); throw new Error(EXCL_ASK); }
        lbExclAsked = false;
        const labels = {}; box.querySelectorAll("select[data-label]").forEach(s => { if (s.value) labels[s.dataset.label] = s.value; });
        const reason = d === "exclude" ? q("#dw-reason", box).value : "", note = q("#dw-labnote", box).value;
        // a blind row carries an opaque item in place of its sample (D6); an unblinded row posts sample and record
        const where = r.item ? { item: r.item } : { sample_id: r.sample_id, record_id: r.record_id };
        const saved = await postCorrectable("/api/labels", { ...where, reviewer: owner, decision: d, reason, labels, note });
        if (window.offerUndo) offerUndo(saved.change_id, `Label saved: ${d}.`);
        Object.assign(r, { decision: d, reason, labels: JSON.stringify(labels), note });
        const tr = q(`#lb-table tr[data-idx="${lb.view.indexOf(r)}"]`);
        if (tr && tr.children) tr.children[2].innerHTML = `<span class="${decClass(d)}">${h(d)}</span>${reason ? ` <span class="muted small">${h(reason)}</span>` : ""}`;
        // the page's count is quiet while the drawer is open: the drawer's own line says what was saved (one announcement)
        const done = lb.rows.filter(x => x.decision).length;
        q("#lb-progress").textContent = `${done} of ${lb.rows.length} labelled · showing ${lb.view.length}`;
        if (row && row.hidden !== undefined && i !== 1 && (codebook().exclusion_reasons || []).length) row.hidden = true;
      } })),
      onMove: (idx) => Drawer.markRow(q("#lb-table"), idx),
      onAnnotate: async () => { lb.ann = await summary(owner, true); },
      onClose: () => { if (pr && pr.removeAttribute) pr.removeAttribute("aria-live"); lbRender(); },   // the "not labelled yet" filter drops what was just labelled
      // Esc goes back to the row of the record last shown, or, when the filter dropped it, to the row after it
      backTo: (r) => { const k = lb.rows.indexOf(r), j = lb.view.findIndex(x => lb.rows.indexOf(x) >= k); return j < 0 ? null : q(`#lb-table tr[data-idx="${j}"]`); },
    });
  }
  q("#lb-load").onclick = () => lbLoad(true);
  q("#lb-filter").onchange = () => { if (lb.rows.length) lbRender(); };
  if (q("#lb-mode")) q("#lb-mode").onchange = () => { if (lb.owner) lbLoad(false); };
  for (const s of ["#lb-sample", "#lb-blind"]) if (q(s)) q(s).onchange = () => { if (lb.owner) lbLoad(false); if (s === "#lb-blind" && typeof loadSamples === "function") loadSamples(); };
  q("#lb-search").oninput = () => { if (lb.rows.length) lbRender(); };
  document.addEventListener("widername", () => window.lbSigning());   // a first name set from the dialog
  // a name switch reloads the page; a browser that restores form state must not hand the next reviewer an unblinded queue (D33)
  if (q("#lb-blind")) q("#lb-blind").checked = true;
  window.lbSigning();

  // =============================================================== adjudication (reviewer versus reviewer)
  let ag = { rows: [] };
  async function agRun() {
    const sample = q("#ag-sample").value, a = q("#ag-a").value.trim(), b = q("#ag-b").value.trim();
    if (!sample) return;
    if (!a || !b) { q("#ag-summary").textContent = "name both reviewers"; return; }
    try {
      const r = await call(`/api/labels/agreement?sample=${encodeURIComponent(sample)}&a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
      // a name with no labels in the sample: the near match or the reviewers who have labels, said once in red
      if (q("#ag-hints")) q("#ag-hints").textContent = (r.hints || []).join(" ");
      const line = (r.n ? `Both labelled ${r.n} record${r.n === 1 ? "" : "s"} · they agree on ${Math.round(r.n * r.agreement)} (${(100 * r.agreement).toFixed(1)}%) · agreement beyond chance ${r.kappa} (Cohen's κ) · ${r.disagreements.length} disagreement${r.disagreements.length === 1 ? "" : "s"}` : "no records labelled by both")
        + (r.left_out ? ` · ${r.left_out.line}` : "");   // a review package's exclude choice, said once
      q("#ag-summary").textContent = line;
      const c = await call(`/api/labels/compare?sample=${encodeURIComponent(sample)}&a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
      // the focus page says the same in words (label-focus-pages): how many both labelled, how many they agree on
      const same = r.n ? Math.round(r.n * r.agreement) : 0;
      const plain = (r.n ? `Both labelled ${r.n} record${r.n === 1 ? "" : "s"} · they agree on ${same} (${(100 * r.agreement).toFixed(1)}%) · agreement beyond chance ${r.kappa} (Cohen's κ)` : "")
        + (r.left_out ? ` · ${r.left_out.line}` : "");
      ag = { rows: c.rows, a, b, sample, locked: c.locked, line: plain };
      agRender();
    } catch (e) { q("#ag-summary").textContent = e.message; }
  }
  function agRender() {
    const t = q("#ag-disagreements");
    t.innerHTML = ag.rows.length ? `<thead><tr><th>title</th><th>${h(ag.a)}</th><th>${h(ag.b)}</th><th>adjudicated</th></tr></thead><tbody>` +
      ag.rows.map((r, i) => `<tr data-idx="${i}"><td>${h(r.title || r.record_id)}${r.different_abstracts ? `<div class="small">${h(r.different_abstracts)}</div>` : ""}</td><td class="${decClass(r.a.decision)}">${h(r.a.decision)}${made(r.a)}</td><td class="${decClass(r.b.decision)}">${h(r.b.decision)}${made(r.b)}</td>
        <td>${r.adjudicated ? `<b class="${decClass(r.adjudicated.decision)}">${h(r.adjudicated.decision)}</b> <span class="muted small">${h(r.adjudicated.decided_by || "")}</span>` : ""}</td></tr>`).join("") + "</tbody>" : "";
    Drawer.bindRows(t, agOpen);
  }
  // how a label was made (review-package D7, D9): in a package (blind or repeat), the blind queue, or in WIDER
  const MADE = { yes: "blind, review package", repeat: "repeat, review package", queue: "blind queue in WIDER", no: "made in WIDER" };
  const madeText = (l) => [MADE[l.blinded], l.left_out ? "left out of agreement" : ""].filter(Boolean).map(h).join(" · ");
  const made = (l) => `<div class="small">${madeText(l)}</div>`;
  const ticks = (t) => Object.entries(t || {}).map(([k, v]) => `${h(k)} ${h(v)}`).join(", ");
  // one reviewer's label as a card, its left edge in the decision's colour, signed with the reviewer's name
  const side = (who, l) => `<section class="dw-side" data-tone="${h(l.decision || "")}"><h3>${h(who)} <span class="${decClass(l.decision)} fx-dword">${h(l.decision)}</span></h3>${l.reason ? `<div>reason: ${h(l.reason)}</div>` : ""}`
    + `${ticks(l.ticks) ? `<div class="small">criteria: ${ticks(l.ticks)}</div>` : ""}${(l.tags || []).length ? `<div class="small">tags: ${h(l.tags.join(", "))}</div>` : ""}`
    + `${l.note ? `<div class="small">note: ${h(l.note)}</div>` : ""}<div class="small">abstract: ${h(l.abstract || "")}</div>`
    + `<p class="fx-sig small">Signed <b>${h(who)}</b>${madeText(l) ? ` · ${madeText(l)}` : ""}</p></section>`;
  function agOpen(i) {
    const by = Drawer.author ? Drawer.author() : "";
    const resolved = () => ag.rows.filter(r => r.adjudicated).length;
    // the adjudicator may be one of the two reviewers: said once, nothing is refused (label-focus-pages D6)
    const own = by && (by === ag.a || by === ag.b) ? ` You labelled this sample as <b>${h(by)}</b> too; your decision is saved as the adjudication.` : "";
    (window.Focus || Drawer).open({   // focus.js; a page without it opens the side drawer
      full: { title: "Adjudicate disagreements", leave: "Leave adjudication", barLabel: "Resolved",
        signing: by ? `deciding as <b>${h(by)}</b>` : "no name yet",
        progress: (r) => ({ text: `Sample ${ag.sample} · ${ag.a} and ${ag.b} · disagreement ${ag.rows.indexOf(r) + 1} of ${ag.rows.length} · ${resolved()} resolved`, done: resolved(), total: ag.rows.length, more: ag.line || "" }),
        note: "Both reviewers labelled these records, so their labels are shown here. The AI model's decisions, the rank and the score are not shown.",
        aside: () => criteria("The criteria") },
      label: `Adjudication: ${ag.a} versus ${ag.b}`, table: "#ag-disagreements", items: ag.rows, index: i,
      recordId: (r) => r.record_id,
      render: async (r, u) => {
        const d = await u.call(`/api/records/${r.record_id}`);
        return `<div class="fx-above"><h2>Their labels</h2><div class="dw-compare">${side(ag.a, r.a)}${side(ag.b, r.b)}</div>`
          + (r.different_abstracts ? `<p>${h(r.different_abstracts)}</p>` : "") + "</div>"
          + u.recordBlock(d.record, [])
          + `<div class="dw-after fx-adj"><p>You decide as <b>${h(by)}</b>, the adjudicator. Your decision is saved as the adjudication; both their labels are kept.${own}</p>`
          + `<label class="small">Reason for the resolution (optional) <input id="ag-adj-reason" type="text" autocomplete="off"></label></div>`;
      },
      current: (r) => r.adjudicated && r.adjudicated.decision,
      sameAs: () => (q("#ag-adj-reason") ? q("#ag-adj-reason").value.trim() : ""),
      decisions: ["include", "exclude", "maybe"].map(d => ({ value: d, label: d[0].toUpperCase() + d.slice(1), tone: d, fn: async (r) => {
        // signed by the name the bar shows: a name set after opening needs the page opened again (risk-checker R2)
        if (!by) throw new Error("enter your name first, then open the disagreement again");   // an adjudication is always attributed
        const why = q("#ag-adj-reason") ? q("#ag-adj-reason").value.trim() : "";
        const saved = await postCorrectable("/api/labels/adjudicate", { sample: ag.sample, record_id: r.record_id, decision: d, by, reason: why });
        if (window.offerUndo) offerUndo(saved.change_id, `Adjudicated: ${d}.`);
        r.adjudicated = { decision: d, decided_by: by };
        agRender();   // the table shows the resolution at once (the drawer keeps its place)
      } })),
      onMove: (idx) => Drawer.markRow(q("#ag-disagreements"), idx),
      onClose: agRender,
    });
  }
  q("#ag-run").onclick = agRun;

  // =============================================================== screening review (inspect a run)
  let ins = { rows: [], view: [], ann: {} };
  const grades = (p) => Object.keys(p || {}).filter(k => /^e\d+$/.test(k)).sort((a, b) => +a.slice(1) - +b.slice(1));
  const quotesOf = (p) => Object.entries(p || {}).filter(([k, v]) => k.endsWith("_quote") && typeof v === "string").map(([, v]) => v);
  async function insLoad() {
    const run = q("#in-run").value; if (!run) return;
    try {
      // development records left out while the development sample is open come with a line in a header (D4)
      let res;
      [res, ins.ann] = await Promise.all([call(`/api/screen/calls/${encodeURIComponent(run)}?limit=2000${q("#in-decision").value ? "&decision=" + encodeURIComponent(q("#in-decision").value) : ""}`, { withHeaders: true }), summary("", false)]);
      ins.rows = Array.isArray(res) ? res : res.body;
      ins.hidden = Array.isArray(res) || !res.headers ? "" : res.headers.get("X-Hidden") || "";
    } catch (e) { q("#in-table").innerHTML = `<tbody><tr><td class="bad">${h(e.message)}</td></tr></tbody>`; return; }
    insRender();
  }
  function insRender() {
    const terms = words(q("#in-search").value);
    ins.view = ins.rows.filter(r => matches(`${r.title || ""} ${(ins.ann[r.record_id] || {}).text || ""} ${((ins.ann[r.record_id] || {}).tags || []).join(" ")}`, terms));
    const t = q("#in-table"), note = q("#in-hidden");
    if (note) { note.textContent = ins.hidden ? `${ins.hidden}.` : ""; note.hidden = !ins.hidden; }
    t.innerHTML = `<thead><tr><th>record</th><th>raw</th><th>rule</th><th>title</th><th>tags</th></tr></thead><tbody>` +
      ins.view.map((r, i) => `<tr data-idx="${i}"><td>${r.record_id}</td><td>${h(r.raw_decision || "")}</td><td class="${decClass(r.rule_decision)}"><b>${h(r.rule_decision || "")}</b></td>
        <td>${h(r.title)}${noteMark(ins.ann[r.record_id])}<div class="muted small">${h((r.parsed || {}).summary || r.error || "")}</div></td><td>${tagChips(ins.ann[r.record_id])}</td></tr>`).join("") + "</tbody>";
    Drawer.bindRows(t, insOpen);
  }
  function modelPanel(p, decision, raw) {
    const g = grades(p);
    return `<div class="dw-model"><p><b>Model:</b> <span class="${decClass(decision)}">${h(decision || "")}</span>${raw && raw !== decision ? ` <span class="muted small">(raw ${h(raw)})</span>` : ""}</p>
      ${p && p.summary ? `<p class="small">${h(p.summary)}</p>` : ""}
      ${g.length ? `<ul class="dw-grades">${g.map(k => `<li><b>${h(k)}</b> ${h(p[k])}${p[k + "_quote"] ? ` <q>${h(p[k + "_quote"])}</q>` : ""}</li>`).join("")}</ul>` : ""}
      ${p && p.missing_for_maybe ? `<p class="small muted">missing: ${h(p.missing_for_maybe)}</p>` : ""}</div>`;
  }
  function insOpen(i) {
    Drawer.open({
      label: `Screening run ${q("#in-run").value}`, table: "#in-table", items: ins.view, index: i,
      recordId: (r) => r.record_id,
      render: async (r, u) => { const d = await u.call(`/api/records/${r.record_id}`); return modelPanel(r.parsed, r.rule_decision, r.raw_decision) + u.recordBlock(d.record, quotesOf(r.parsed)); },
      onMove: (idx) => Drawer.markRow(q("#in-table"), idx),
      onAnnotate: async () => { ins.ann = await summary("", false); },
      onClose: insRender,
    });
  }
  q("#in-btn").onclick = insLoad;
  q("#in-search").oninput = () => { if (ins.rows.length) insRender(); };

  // =============================================================== development evaluation (LLM versus human)
  let ev = { rows: [], view: [] };
  async function evLoad() {
    const run = q("#ev-run").value, sample = q("#ev-sample").value; if (!run) return;
    try {
      const r = await call(`/api/screen/evaluate/${encodeURIComponent(run)}${sample ? "?sample=" + encodeURIComponent(sample) : ""}`);
      q("#ev-summary").textContent = `n=${r.n} · must not miss ${r.must_not_miss} · misses ${r.misses} · false includes ${r.false_includes} · false maybes ${r.false_maybes} · reasons agree ${r.reason_agreement}`;
      // whose labels stand where reviewers disagree, and an unlocked sample: said once, in normal black text (D9)
      q("#ev-lines").innerHTML = (r.lines || []).map(l => `<p data-ev-line>${h(l)}</p>`).join("");
      ev.rows = r.disagreements;
      evRender();
    } catch (e) { q("#ev-summary").textContent = e.message; q("#ev-lines").innerHTML = ""; }
  }
  function evRender() {
    const f = q("#ev-filter").value;
    ev.view = ev.rows.filter(r => !f || r.category === f);
    const t = q("#ev-table");
    t.innerHTML = ev.view.length ? `<thead><tr><th>category</th><th>pool</th><th>human</th><th>model</th><th>title</th></tr></thead><tbody>` +
      ev.view.map((r, i) => `<tr data-idx="${i}"><td class="${r.category === "MISS" ? "bad" : ""}">${h(r.category)}</td><td class="small">${h(r.pool || "")}</td>
        <td>${h(r.human)} <span class="muted small">${h(r.human_reason || "")}</span></td><td>${h(r.model)}</td><td>${h(r.title)}<div class="muted small">${h(r.summary || "")}</div></td></tr>`).join("") + "</tbody>" : "";
    Drawer.bindRows(t, evOpen);
  }
  function evOpen(i) {
    Drawer.open({
      label: "LLM versus human", table: "#ev-table", items: ev.view, index: i,
      recordId: (r) => r.record_id,
      render: async (r, u) => {
        const d = await u.call(`/api/records/${r.record_id}`);
        const qs = Object.values(r.quotes || {});
        return `<div class="dw-compare"><div class="dw-side"><b>Human</b>: <span class="${decClass(r.human)}">${h(r.human)}</span>${r.human_reason ? ` · ${h(r.human_reason)}` : ""}</div>
          <div class="dw-side"><b>Model</b>: <span class="${decClass(r.model)}">${h(r.model)}</span> · <span class="${r.category === "MISS" ? "bad" : ""}">${h(r.category)}</span></div></div>
          ${r.summary ? `<p class="small">${h(r.summary)}</p>` : ""}
          ${qs.length ? `<ul class="dw-grades">${Object.entries(r.quotes).map(([k, v]) => `<li><b>${h(k)}</b> <q>${h(v)}</q></li>`).join("")}</ul>` : ""}
          ${r.missing_for_maybe ? `<p class="small muted">missing: ${h(r.missing_for_maybe)}</p>` : ""}` + u.recordBlock(d.record, qs);
      },
      onMove: (idx) => Drawer.markRow(q("#ev-table"), idx),
    });
  }
  q("#ev-run-btn").onclick = evLoad;
  q("#ev-filter").onchange = evRender;

  // =============================================================== notes and tags search (home page)
  let an = { rows: [] }, anTimer = null;
  async function anLoad() {
    const params = new URLSearchParams();
    if (q("#an-search").value.trim()) params.set("q", q("#an-search").value.trim());
    if (q("#an-tag").value) params.set("tag", q("#an-tag").value);
    if (q("#an-author").value.trim()) params.set("author", q("#an-author").value.trim());
    let d;
    try { d = await call("/api/annotations?" + params.toString()); } catch (e) { q("#an-table").innerHTML = `<tbody><tr><td class="bad">${h(e.message)}</td></tr></tbody>`; return; }
    const cur = q("#an-tag").value;
    q("#an-tag").innerHTML = `<option value="">any tag</option>` + d.tags.map(t => `<option value="${h(t.tag)}"${t.tag === cur ? " selected" : ""}>${h(t.tag)} (${t.n})</option>`).join("");
    an.rows = d.rows;
    const t = q("#an-table");
    t.innerHTML = an.rows.length ? `<thead><tr><th>record</th><th>title</th><th>tags</th><th>notes</th></tr></thead><tbody>` +
      an.rows.map((r, i) => `<tr data-idx="${i}"><td>${r.record_id}</td><td>${h(r.title || "")}<div class="muted small">${h([r.year, r.journal].filter(Boolean).join(" · "))}</div></td>
        <td>${[...new Set(r.tags.map(x => x.tag))].map(x => `<span class="chip tag-chip">${h(x)}</span>`).join(" ")}</td>
        <td class="small">${r.notes.map(n => `<div><span class="muted">${h(n.author)}:</span> ${h(n.text)}</div>`).join("")}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="muted">${params.toString() ? "nothing matches" : "No notes or tags yet. Add them in the record drawer on the labelling, screening or evaluation tables."}</td></tr></tbody>`;
    Drawer.bindRows(t, (i) => Drawer.open({
      label: "Notes and tags", table: "#an-table", items: an.rows, index: i, recordId: (r) => r.record_id,
      render: async (r, u) => { const d2 = await u.call(`/api/records/${r.record_id}`); return u.recordBlock(d2.record, []); },
      onMove: (idx) => Drawer.markRow(q("#an-table"), idx),
      onClose: anLoad,
    }));
  }
  const anLater = () => { clearTimeout(anTimer); anTimer = setTimeout(anLoad, 250); };
  q("#an-search").oninput = anLater; q("#an-author").oninput = anLater; q("#an-tag").onchange = anLoad;
  window.loadNotesSearch = anLoad;

  // after an undo the tables reload from the store (the drawer was closed by the undo)
  window.addEventListener("wider:undone", () => {
    if (lb.rows.length) lbLoad(false);
    if (ag.rows.length) agRun();
    if (ins.rows.length) insLoad();
    if (ev.rows.length) evLoad();
  });
})();
