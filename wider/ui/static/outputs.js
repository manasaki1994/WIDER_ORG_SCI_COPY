// Outputs (WP14): search-strings archive on the query page; PRISMA 2020 diagram, methods paragraph, reference exports
// and replication package on the recall page. Files open through /api/outputs/file (exports folder only).
(function () {
  const q = (s) => document.querySelector(s);
  const h = esc;
  const call = api;
  const fileLink = (path) => `<a href="/api/outputs/file?path=${encodeURIComponent(path)}" download>${h(path.split("/").pop())}</a>`;
  const protocol = () => (q("#rc-protocol") && q("#rc-protocol").value) || "";
  const rset = () => (protocol() && q("#rc-set") && q("#rc-set").value) || "";   // the set chosen on the Recall page
  const pq = () => protocol() ? `protocol=${encodeURIComponent(protocol())}${rset() ? "&result_set=" + encodeURIComponent(rset()) : ""}` : "";
  const say = (sel, html, bad) => { q(sel).innerHTML = bad ? `<span class="bad">${h(html)}</span>` : html; };
  const done = () => { if (window.afterAction) afterAction(); };   // each output records a recall result

  // ---------------------------------------------------------------- search-strings archive (query page)
  async function loadArchive() {
    let d;
    try { d = await call("/api/query/archive"); } catch (e) { q("#q-archive-table").innerHTML = ""; return; }
    q("#q-archive-table").innerHTML = d.entries.length ? `<tr><th>archived</th><th>platform</th><th>synonyms</th><th>reason</th><th>file</th><th>translation notes</th></tr>` +
      d.entries.slice().reverse().map(e => `<tr><td class="small">${h(fmtWhen(e.archived_at))}</td><td>${h(e.label)}</td>
        <td>v${h(e.synonyms_version)} <span class="muted small">${h(e.synonyms_status)}</span></td><td class="small">${h(e.reason)}</td>
        <td class="small">${fileLink("exports/search_strings/" + e.file)}</td>
        <td class="small" data-archive-ledger>${e.ledger ? fileLink("exports/search_strings/" + e.ledger) : '<span class="muted">none (archived before translation notes were kept)</span>'}</td></tr>`).join("") : `<tr><td class="muted">nothing archived yet</td></tr>`;
  }
  q("#q-archive").onclick = async () => {
    try { const r = await call("/api/query/archive", { method: "POST" }); say("#q-archive-msg", r.n_new ? `${r.n_new} new string${r.n_new === 1 ? "" : "s"} archived` : "already archived, nothing new"); await loadArchive(); }
    catch (e) { say("#q-archive-msg", e.message, true); }
  };

  // ---------------------------------------------------------------- recall page outputs
  q("#out-prisma").onclick = async () => {
    say("#out-prisma-msg", "drawing…");
    try {
      const r = await call(`/api/outputs/prisma?${pq()}`, { method: "POST" });
      say("#out-prisma-msg", `${fileLink(r.path)} · ${fileLink("exports/prisma_2020.json")}`); done();
    } catch (e) { say("#out-prisma-msg", e.message, true); }
  };
  // the plain corpus export (ui-redesign D4): every record, bibliographic data only, while labelling is open too
  q("#out-corpus").onclick = async () => {
    say("#out-corpus-msg", "writing…");
    try {
      const r = await call("/api/outputs/corpus", { method: "POST" });
      say("#out-corpus-msg", `${h(r.n)} records: ${r.files.map(fileLink).join(" · ")}`); done();
    } catch (e) { say("#out-corpus-msg", e.message, true); }
  };
  // the PRISMA files (2c D17, D7): the items themselves are shown and edited on PRISMA reporting (owner, 30 Oct,
  // D32 F17), so the Recall page names only the message and the files written
  const filesLine = (r) => `${h(r.message)} · ${fileLink(r.path)} · ${fileLink(r.json_path)}`;
  async function writePrismaS() {
    say("#out-prisma-s-msg", "writing…");
    try {
      const r = await call("/api/outputs/prisma-s", { method: "POST" });
      say("#out-prisma-s-msg", filesLine(r));
    } catch (e) { say("#out-prisma-s-msg", e.message, true); }
  }
  q("#out-prisma-s").onclick = writePrismaS;
  async function writePrisma2020() {
    say("#out-prisma-2020-msg", "writing…");
    try {
      const r = await call(`/api/outputs/prisma-checklist?${pq()}`, { method: "POST" });
      say("#out-prisma-2020-msg", filesLine(r)); done();
    } catch (e) { say("#out-prisma-2020-msg", e.message, true); }
  }
  q("#out-prisma-2020").onclick = writePrisma2020;
  async function loadValidity() {
    let d;
    try { d = await call("/api/outputs/validity"); } catch (e) { q("#out-validity").innerHTML = ""; return; }
    q("#out-validity").innerHTML = d.text.length
      ? d.text.map((t, i) => i ? `<p class="small">${h(t)}</p>` : `<p><strong>${h(t)}</strong></p>`).join("")
      : `<p class="muted small">No seed papers yet, so there is nothing to validate the search on.</p>`;
  }
  // the independent check (D25): its lines as the CLI prints them; the caveat in normal text, said once
  async function loadIndependent() {
    if (!q("#out-independent")) return;
    let d;
    const pr = protocol() ? `protocol=${encodeURIComponent(protocol())}` : "";   // that protocol's active set
    try { d = await call(`/api/coverage/independent?${pr}`); } catch (e) { say("#out-independent", e.message, true); return; }
    q("#out-independent").innerHTML = d.lines.length   // every line normal size: the caveats are warnings
      ? d.lines.map((t, i) => i ? `<p>${h(t)}</p>` : `<p><strong>${h(t)}</strong></p>`).join("")
      : `<p class="muted small">No seed papers outside the query's results yet.</p>`;
  }
  async function loadMethods() {
    try { const d = await call("/api/outputs/methods"); if (d.text != null && !q("#out-methods-text").value) { q("#out-methods-text").value = d.text; say("#out-methods-msg", `loaded ${h(d.file)}`); } }
    catch (e) { /* no project yet */ }
  }
  // a recall set waiting for the choice of D8: the notice in normal text and the two choices beside it; a press
  // repeats the action with that choice (nothing pre-selected, nothing done until one is pressed)
  function pendingChoice(sel, notice, again) {
    q(sel).innerHTML = `<p>${h(notice)}</p><button class="secondary" data-choice="used">Sample used</button> <button class="secondary" data-choice="not_informed">Not informed</button>`;
    q(sel).querySelectorAll("[data-choice]").forEach(b => { b.onclick = () => again(b.dataset.choice); });
  }
  const withChoice = (c) => c ? `&choice=${encodeURIComponent(c)}` : "";
  async function draftMethods(choice) {
    say("#out-methods-msg", "drafting…");
    try {
      const d = await call(`/api/outputs/methods/draft?${pq()}${withChoice(choice)}`); q("#out-methods-text").value = d.draft;
      if (d.choice_needed) pendingChoice("#out-methods-msg", d.notice, draftMethods);
      else say("#out-methods-msg", "draft from the project's facts; rewrite it in your words, then save");
    } catch (e) { say("#out-methods-msg", e.message, true); }
  }
  q("#out-methods-draft").onclick = async () => {
    if (q("#out-methods-text").value.trim() && !(await askConfirm("Replace the text in the box with a fresh draft? (the saved paragraph changes only when you save)", "Replace"))) return;
    await draftMethods(null);
  };
  q("#out-methods-save").onclick = async () => {
    try { const r = await call("/api/outputs/methods", { method: "POST", body: JSON.stringify({ text: q("#out-methods-text").value }) }); say("#out-methods-msg", `saved ${h(r.file)}`); }
    catch (e) { say("#out-methods-msg", e.message, true); }
  };
  async function buildPackage(choice) {
    say("#out-package-msg", "building…");
    try {
      const sample = q("#rc-sample") && q("#rc-sample").value;
      const r = await call(`/api/outputs/package?${pq()}${sample ? "&sample=" + encodeURIComponent(sample) : ""}${withChoice(choice)}`, { method: "POST" });
      if (r.choice_needed) { pendingChoice("#out-package-msg", r.notice, buildPackage); return; }
      say("#out-package-msg", `${fileLink(r.path)} · ${r.n_files} files · ${Math.round(r.size_bytes / 1024)} KB`); done();
    } catch (e) { say("#out-package-msg", e.message, true); }
  }
  q("#out-package").onclick = () => buildPackage(null);

  // load with their pages
  window.addEventListener("hashchange", () => { const k = location.hash.slice(1); if (k === "query") loadArchive(); if (k === "recall") { loadMethods(); loadValidity(); loadIndependent(); } });
  if (location.hash === "#query") loadArchive();
  if (location.hash === "#recall") { loadMethods(); loadValidity(); loadIndependent(); }
  if (q("#rc-protocol")) q("#rc-protocol").addEventListener("change", loadIndependent);   // the estimate reads its includes
  window.loadOutputs = { loadArchive, loadMethods, loadValidity, loadIndependent, writePrismaS, writePrisma2020 };
})();
