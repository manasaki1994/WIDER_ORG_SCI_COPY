// The "Diagnostic report" card on the Search results page (D9): what each block and term retrieved in the imported
// exports, the records no term explains, recall per seed set and the noise terms, then the clusters with a Relevant and
// a Noise button. Same engine as `wider query diagnose` / `wider query clusters` / `wider query cluster` (GET
// /api/query/diagnose, POST /api/query/cluster). A mark is the researcher's judgement only; nothing is screened by it.
(function () {
  // A pure builder: one platform's counts, recall, noise and clusters.
  function platformHtml(key, pl) {
    if (!pl.profile) return `<h3>${esc(key)}</h3><p class="muted small">${esc(pl.note)}</p>`;
    // a sampled file (D6): the engine sends each count as an estimate sentence ("about 156,000 records (300 in the
    // sample)"), shown as sent so the card and `wider query diagnose` read the same
    const est = !!pl.estimated;
    const terms = pl.terms.map(t => `<tr><td class="small">${esc(t.block)}: ${esc(t.term)}</td><td>${esc(est ? t.text : t.records)}</td>
      <td>${t.only ? esc(est ? t.only_text : t.only) : ""}</td></tr>`).join("");
    const blocks = pl.blocks.map(b => `block ${esc(b.block)}: ${est ? esc(b.text) : `${esc(b.records)} records`}`).join(" · ");
    const recall = Object.entries(pl.recall).map(([g, r]) => {
      const name = g === "Validation, used" ? "used validation (not independent)" : g.toLowerCase();
      if (r.sampled) return `<li data-recall-set="${esc(g)}">${esc(name)} seeds in the sample: ${esc(r.text)}</li>`;
      const ci = r.interval ? `, 95% interval ${r.interval[0].toFixed(2)} to ${r.interval[1].toFixed(2)}` : "";
      const miss = r.missed.length ? `; missed ${r.missed.join(", ")}` : "";
      return `<li data-recall-set="${esc(g)}">${esc(name)} seeds found: ${esc(r.found)} of ${esc(r.total)}${esc(ci)}${esc(miss)}</li>`;
    }).join("");
    // the missed seeds by the researcher's indexing verdict (the Indexing check card sets them; D10)
    const groupLabel = { not_indexed: "not in the database", indexed_missed: "indexed but not retrieved",
      unknown: "indexing unknown", unchecked: "indexing not checked yet" };
    const idx = Object.entries(pl.indexing || {});
    const indexing = idx.length ? `<ul class="small" data-indexing="${esc(key)}">${idx.map(([g, ids]) =>
      `<li data-indexing-group="${esc(g)}">${esc(groupLabel[g] || g)}: ${esc(ids.join(", "))}</li>`).join("")}</ul>` : "";
    const noise = pl.noise.length
      ? `<ul class="small">${pl.noise.map(x => `<li data-noise="${esc(x.block)}: ${esc(x.term)}">${esc(x.block)}: ${esc(x.term)}: ${x.text ? esc(x.text) : `${esc(x.records)} records, ${(100 * x.share).toFixed(1)}% of ${esc(key)}`}</li>`).join("")}</ul>`
      : `<p class="small">none</p>`;
    // the heading says the counts are estimates: at normal size, not muted (D6)
    const head = est ? `<h3>${esc(key)}</h3><p data-sample-heading="${esc(key)}">${esc(pl.heading)}</p>`
      : `<h3>${esc(key)} <span class="muted small">${esc(pl.records)} records</span></h3>`;
    return `${head}
      <p class="small">${blocks}</p>
      <table class="dr-table" data-diag="${esc(key)}"><tr><th>term</th><th>records</th><th>by this term alone (in its block)</th></tr>${terms}</table>
      <p class="small" data-no-term="${esc(key)}">no term explains: ${esc(est ? pl.no_term_text : pl.no_term)} (${esc(pl.no_term_note)})${pl.uncertain ? ` · uncertain under the profile: ${esc(est ? pl.uncertain_text : pl.uncertain)}` : ""}</p>
      <ul class="small">${recall}</ul>${indexing}
      <p class="small"><b>Noise</b> (no seed and no cluster marked relevant shares a term of the same block with these records)</p>${noise}
      ${clustersHtml(key, pl.clusters)}`;
  }

  function clustersHtml(key, clusters) {
    const rows = clusters.map(c => {
      const mark = c.mark ? `<span class="badge ${c.mark === "relevant" ? "ok" : "warn"}">${esc(c.mark)}${c.by ? ` by ${esc(c.by)}` : ""}</span>` : "";
      const btn = (m, label) => `<button class="secondary small-btn" data-cluster="${esc(c.id)}" data-platform="${esc(key)}" data-mark="${m}"
        aria-label="Mark ${esc(c.id)} (${esc(c.label)}) ${m}"${c.mark === m ? " disabled" : ""}>${label}</button>`;
      return `<tr data-cluster-row="${esc(c.id)}"><td>${esc(c.id)}</td><td>${esc(c.text || c.size)}</td><td class="small">${esc(c.label)}</td>
        <td class="small">${esc(c.samples.join(" · "))}</td><td class="small">${esc(c.seeds.join(", "))}</td>
        <td>${mark} ${btn("relevant", "Relevant")} ${btn("noise", "Noise")}</td></tr>`;
    }).join("");
    return `<details class="diag-clusters" data-clusters="${esc(key)}" open><summary>Clusters (${esc(clusters.length)})</summary>
      <table class="dr-table"><tr><th>id</th><th>records</th><th>terms</th><th>sample titles</th><th>seeds</th><th>your mark</th></tr>${rows}</table></details>`;
  }

  function resultHtml(d) {
    const hidden = d.validation_shown ? "" : ` <span class="muted">(validation set hidden: development seeds only)</span>`;
    // an estimated budget line carries the warning that the numbers are estimates: normal size (D6)
    const bud = d.budget ? `<p${d.budget.estimated ? "" : ` class="small"`} data-budget>${esc(d.budget.text)}</p>` : "";
    const counted = Object.values(d.platforms).some(pl => pl.recall && Object.keys(pl.recall).length);
    const known = counted && d.known_papers_note ? `<p class="small muted" data-known-note>${esc(d.known_papers_note)}</p>` : "";
    return `<p class="small">synonyms v${esc(d.version)}${hidden}</p>${bud}` +
      Object.entries(d.platforms).map(([k, pl]) => platformHtml(k, pl)).join("") + known;
  }

  function params() {
    const v = ($("#diag-version") || {}).value || "", p = ($("#diag-platform") || {}).value || "";
    const q = new URLSearchParams();
    if (v.trim()) q.set("version", v.trim());
    if (p.trim()) q.set("platform", p.trim());
    return q;
  }

  // The suggested edits (`wider query edits`): one row per edit with its consequence and an Apply button; the list
  // works without an import (the consequence then comes from the seed papers alone).
  function editsHtml(d) {
    const rows = (d.edits || []).map(e => `<tr data-edit-row="${esc(e.id)}"><td>${esc(e.id)}</td><td class="small">${esc(e.label)}</td>
      <td class="small">${esc(e.source)}</td><td class="small">${esc(e.consequence)}</td>
      <td><button class="secondary small-btn" data-edit="${esc(e.id)}" aria-label="Apply ${esc(e.id)} (${esc(e.label)})">Apply</button></td></tr>`).join("");
    return `<h3>Suggested edits <span class="muted small">synonyms v${esc(d.version)} (${esc(d.status)})</span></h3>
      <p${d.budget.estimated ? "" : ` class="small"`} data-edits-budget>${esc(d.budget.text)}</p>
      ${rows ? `<table class="dr-table" data-edits><tr><th>id</th><th>edit</th><th>from</th><th>consequence</th><th></th></tr>${rows}</table>`
             : `<p class="small muted">no edit to suggest</p>`}
      <p class="muted small">Applying writes a new draft synonyms version whose note names the edit; nothing else changes.</p>
      ${cmdFold("<code>wider query edits</code>")}`;
  }

  async function loadEdits() {
    const box = $("#diag-edits"); if (!box) return;
    const v = ($("#diag-version") || {}).value || "";
    try {
      const d = await api(`/api/query/edits${v.trim() ? `?version=${encodeURIComponent(v.trim())}` : ""}`);
      box.innerHTML = editsHtml(d);
      box.querySelectorAll("[data-edit]").forEach(b => { b.onclick = () => applyEdit(b, d.version); });
    } catch (e) { box.innerHTML = `<p class="small muted">${esc(e.message)}</p>`; }
  }

  async function applyEdit(b, version) {
    b.disabled = true;
    try {
      const go = window.Refine ? await Refine.confirmSave("synonyms", `Apply ${b.dataset.edit} as a new synonyms version?`, "Apply") : true;
      if (!go) { b.disabled = false; return; }
      const body = { id: b.dataset.edit, version };
      if (typeof go === "string") body.seen_choice = go;
      const r = await api("/api/query/edit", { method: "POST", body: JSON.stringify(body) });
      $("#diag-msg").textContent = `saved draft synonyms v${r.version} from v${r.from_version}` + (r.notice ? ` · ${r.notice}` : "");
      await loadEdits();
    } catch (e) { b.disabled = false; $("#diag-msg").textContent = e.message; }
  }

  // Names from connected papers the query misses (`wider query missed-names`, D24): their own block under the
  // suggested edits, each with its consequence and the candidates' titles; "Add to block" goes through the admission
  // rule (`/api/seeds/names/admit`) with the note "from connected candidates ..." and the researcher's words.
  function missedHtml(d) {
    const lo = d.left_out;
    const rows = (d.names || []).map((n, i) => `<li data-missed="${i}"><b>${esc(n.name)}</b>${n.origin.includes("llm") ? ` <span class="small">[${esc(n.origin.join(", "))}]</span>` : ""}
      → block ${esc(n.block)}: ${esc(n.line)}
      <ul class="small">${n.candidates.map(c => `<li>${esc(c.candidate_id)} (${esc(c.status)}, ${esc(c.n_linking)} seeds): “${esc(c.title)}”</li>`).join("")}</ul>
      <label class="small">Note <input type="text" size="60" data-missed-note="${i}" value="${esc(n.note)}; "></label>
      <button class="secondary small-btn" data-missed-add="${i}" aria-label="Add ${esc(n.name)} to block ${esc(n.block)}">Add to block</button></li>`).join("");
    return `<h3>Names from connected papers <span class="muted small">synonyms v${esc(d.version)} ${esc(d.where || "on " + d.platform)}</span></h3>
      <p class="small">${esc(d.evaluated)} connected candidate(s) linked to ${esc(d.min_links)}+ seeds evaluated, ${esc(d.missed)} missed by the query; left out: ${esc(lo.validation)} Validation, ${esc(lo.dismissed)} dismissed, ${esc(lo.few_links)} with fewer links.${d.uncertain ? ` ${esc(d.uncertain)} candidate(s) uncertain in the dry run (the profile cannot decide, often no abstract) are not used.` : ""}</p>
      ${d.model ? `<p class="small" data-missed-model>${esc(d.model.message)}</p>` : ""}
      ${rows ? `<ol data-missed-list>${rows}</ol>` : `<p class="small">No name: no word is shared by two or more missed candidates and missing from every block.</p>`}
      ${d.more ? `<p class="small">${esc(d.more)} more name(s) not shown.</p>` : ""}
      <p>Nothing changes until you add a name. The consequence comes from the dry run; it is not a judgement of the papers.</p>`;
  }

  function missedControls() {
    return `<p class="small"><label>Linked to at least <input type="number" id="missed-min" min="1" value="2" style="width:4em"> seeds</label>
      <label><input type="checkbox" id="missed-model"> Use the model (titles, abstracts and keywords go to the search-terms model)</label>
      <button class="secondary small-btn" id="missed-run">Find names</button> <span id="missed-msg" role="status"></span></p><p class="send-note" data-send="missed_names" hidden></p><div id="missed-out"></div>`;
  }

  async function loadMissed() {
    const box = $("#diag-missed"); if (!box) return;
    if (!$("#missed-out")) { box.innerHTML = missedControls(); $("#missed-run").onclick = () => runMissed(); }
    await runMissed();
  }

  let missed = null;
  async function runMissed() {
    const out = $("#missed-out"), msg = $("#missed-msg");
    const v = ($("#diag-version") || {}).value || "", p = ($("#diag-platform") || {}).value || "";
    const min = Number(($("#missed-min") || {}).value || 2) || 2, model = !!($("#missed-model") || {}).checked;
    msg.textContent = model ? "Asking the model…" : "";
    try {
      const d = model
        ? await api("/api/query/missed-names", { method: "POST", body: JSON.stringify({
            version: v.trim() ? Number(v) : null, platform: p.trim() || null, min_links: min, model: true }) })
        : await api(`/api/query/missed-names?${new URLSearchParams(Object.assign({ min_links: String(min) },
            v.trim() ? { version: v.trim() } : {}, p.trim() ? { platform: p.trim() } : {}))}`);
      missed = d;
      out.innerHTML = missedHtml(d);
      msg.textContent = "";
      out.querySelectorAll("[data-missed-add]").forEach(b => { b.onclick = () => addMissed(b, Number(b.dataset.missedAdd)); });
    } catch (e) { out.innerHTML = ""; msg.textContent = e.message; }
  }

  async function addMissed(b, i) {
    const n = missed.names[i];
    const note = (($(`[data-missed-note="${i}"]`) || {}).value || "").trim().replace(/;\s*$/, "") || n.note;
    b.disabled = true;
    try {
      const go = window.Refine ? await Refine.confirmSave("synonyms", `Add "${n.name}" to block ${n.block}?`, "Add", true) : true;
      if (!go) { b.disabled = false; $("#missed-msg").textContent = "Nothing saved."; return; }
      const body = { name: n.name, block: n.block, note };
      if (typeof go === "string") body.seen_choice = go;
      const r = await api("/api/seeds/names/admit", { method: "POST", body: JSON.stringify(body) });
      await loadEdits();
      await runMissed();   // clears the message, so the saved line is written after it
      $("#missed-msg").textContent = `${r.notice ? r.notice + " " : ""}synonyms v${r.version} saved: ${r.note}`;
    } catch (e) { b.disabled = false; $("#missed-msg").textContent = e.message; }
  }

  async function load(note) {
    const out = $("#diag-out"), msg = $("#diag-msg"); if (!out) return;
    loadEdits();
    loadMissed();
    try {
      const d = await api(`/api/query/diagnose?${params()}`);
      // nothing imported against a synonyms version yet: an expected state (200), said once in the message line
      if (d.state === "nothing_yet") { out.innerHTML = ""; msg.textContent = [note, `Nothing to diagnose yet: ${d.message}.`].filter(Boolean).join(" · "); return; }
      out.innerHTML = resultHtml(d);
      msg.textContent = [note, d.interview_notice].filter(Boolean).join(" · ");   // questions this diagnosis raised (2b)
      out.querySelectorAll("[data-cluster]").forEach(b => { b.onclick = () => mark(b); });
    } catch (e) { out.innerHTML = ""; msg.textContent = e.message; }
  }

  async function mark(b) {
    b.disabled = true;
    const v = ($("#diag-version") || {}).value || "";
    try {
      const r = await api("/api/query/cluster", { method: "POST", body: JSON.stringify({
        key: b.dataset.cluster, mark: b.dataset.mark, platform: b.dataset.platform, version: v.trim() ? Number(v) : null }) });
      await load(`${r.platform} ${r.id} marked ${r.mark}. No record is screened by it; undo it in History.`);
    } catch (e) { b.disabled = false; $("#diag-msg").textContent = e.message; }
  }

  const b = $("#diag-run"); if (b) b.onclick = () => load();
  window.Diagnosis = { load, mark, platformHtml, clustersHtml, resultHtml, editsHtml, loadEdits, applyEdit, missedHtml, loadMissed, runMissed, addMissed };
})();
