// The Review plan (review-plan-and-question-window D2-D4): five task cards drawn from GET /api/plan, and the focused
// task pages at #setup?task=<key> (question, seeds, prior, databases, more); and the Advanced protocol card on Settings
// (loadProtocol). The LLM backends are on the model page (model.js), the embedding models on the Ranking page
// (pickers.js). Loaded after app.js; app.js calls renderSetupCards(projectPayload) from loadProject().
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const call = api;
  // Every write tells app.js that project.yaml changed, so the task cards and the stage nav refresh.
  const changed = (r) => { window.dispatchEvent(new CustomEvent("wider:changed")); return r; };
  const post = (path, body) => call(path, { method: "POST", body: JSON.stringify(body || {}) }).then(changed);
  const del = (path) => call(path, { method: "DELETE" }).then(changed);
  const opt = (values, current, labels) => values.map((v, i) => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(labels ? labels[i] : v)}</option>`).join("");
  const badge = (text, cls) => `<span class="badge ${cls || ""}">${esc(text)}</span>`;
  // rows already seeds (or repeated) are named, never dropped silently (M8): "; 3 skipped ...: 10.1/x (already seed B002 in Development)"
  const skippedText = (r) => (r.skipped || []).length
    ? `; ${r.skipped_summary}: ${r.skipped.slice(0, 5).map(s => `${s.label} (${s.reason})`).join("; ")}${r.skipped.length > 5 ? "; …" : ""}`
    : "";
  const warned = (notices) => { const ids = Object.keys(notices || {}); return ids.length ? `; see the note beside ${ids.join(", ")}` : ""; };
  const flash = (el, msg, bad) => { if (!el) return; el.innerHTML = bad ? `<span class="bad">${esc(msg)}</span>` : esc(msg); };
  const wrap = (fn, msgEl) => async (...a) => { try { await fn(...a); } catch (e) { flash(msgEl, e.message, true); } };
  // A move of a validation paper that has been shown (validation-reuse D3, D4): the route answers confirm_needed with
  // the notice and does nothing; the dialog asks (focus on Cancel) and only "Move to Development" sends it again.
  // Resolves to the route's answer, or null when cancelled (nothing moved).
  const moveDialogHtml = (notice) => `<h2 id="choice-title">Move to Development?</h2><p>${esc(notice)}</p>
    <div class="row"><button type="button" data-act="move">Move to Development</button>
    <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  async function confirmedPost(path, body) {
    const r = await post(path, body);
    if (!r || !r.confirm_needed) return r;
    const pick = await askChoice(moveDialogHtml(r.notice));
    return pick && pick.act === "move" ? post(path, { ...body, confirm: true }) : null;
  }

  // ------------------------------------------------------------------ seed papers
  let lastSplit = null;
  // Where a seed paper came from; prior_review is offered first, the default stays researcher.
  const SOURCE_LABELS = { prior_review: "a prior review", researcher: "the researcher (default)", colleague: "a colleague",
    other_database: "another database", query_results: "the query's results" };
  // A seed's keywords (D6): typed here (saved on change, journalled) or copied from an import ("from import").
  const keywordsCell = (r) => `<input class="small" data-keywords="${esc(r.benchmark_id)}" value="${esc(r.keywords || "")}"
      placeholder="a; b" aria-label="keywords of ${esc(r.benchmark_id)}">${r.keywords && r.keywords_from === "import" ? '<div class="muted small">from import</div>' : ""}`;
  const sourceOpts = (sources, current) => {
    const vals = current && !sources.includes(current) ? [...sources, current] : sources;
    return opt(vals, current || "researcher", vals.map(v => SOURCE_LABELS[v] || v));
  };
  // ``entry``: notices by paper id that hold only as a paper enters (an add or a resolve that fills its DOI); the plain
  // list leaves out "the query already finds it" beside a hidden Validation paper, so they are shown once, on entry
  // The full-text PDF per seed (change 2b): kept in seeds/ on this computer, never in a bundle, package or export.
  const fullTextCell = (r, f) => f && f.has_pdf
    ? `${badge("PDF", "ok")}${f.extracted ? ' <span class="muted small">text extracted</span>' : ""}
       <button class="secondary small-btn" data-pdf-remove="${esc(r.benchmark_id)}" aria-label="Remove the PDF of ${esc(r.benchmark_id)}">Remove PDF</button>`
    : `<input type="file" accept=".pdf,application/pdf" data-pdf-add="${esc(r.benchmark_id)}" aria-label="PDF of ${esc(r.benchmark_id)}">`;
  async function loadSeed(entry) {
    const box = q("#setup-seed"); if (!box) return;
    const d = await call("/api/benchmark/seed");
    const files = Object.fromEntries(((await call("/api/seeds/files").catch(() => ({ files: [] }))).files || []).map(f => [f.benchmark_id, f]));
    const more = (r) => (entry && Array.isArray(entry[r.benchmark_id]) ? entry[r.benchmark_id] : []).filter(n => !(r.notices || []).includes(n));
    const rows = d.rows.map(r => more(r).length ? { ...r, notices: [...(r.notices || []), ...more(r)] } : r);
    const nDev = rows.filter(r => r.set_name === "Development").length;
    const status = (r) => r.resolved === "resolved" ? badge("resolved", "ok") : r.resolved === "unresolved" ? badge("unresolved", "bad") : badge("pending");
    box.innerHTML = `
      <h2>Seed papers</h2>
      <p class="muted">Papers you already know belong in the review. They ground the concepts and synonyms and are the first recall check.
        <b>${rows.length}</b> seed papers: ${nDev} development, ${rows.length - nDev} validation${d.n_used ? ` (${d.n_used} used)` : ""}.</p>
      ${d.n_used ? `<div class="row"><button class="secondary small-btn" id="seed-move-used">Move used validation papers to Development</button>
        <span class="muted small">${d.n_used === 1 ? "1 validation paper was" : `${d.n_used} validation papers were`} shown before the query was revised; recall on ${d.n_used === 1 ? "it" : "them"} is not independent.</span></div>` : ""}
      ${d.invite ? `<p class="warnings small" id="seed-invite" tabindex="-1">${esc(d.invite)}</p>` : ""}
      <div class="form-grid">
        <label>Paste DOIs or titles, one per line
          <textarea id="seed-text" rows="3" placeholder="10.1000/example.123&#10;A paper title to look up"></textarea></label>
        <div class="row">
          <label class="inline">add to <select id="seed-set">${opt(["Development", "Validation"])}</select></label>
          <label class="inline">source <select id="seed-source">${sourceOpts(d.sources, "researcher")}</select></label>
          <button id="seed-add" class="secondary small-btn">Add</button>
          <label class="inline">or a file (CSV with title/doi columns, or RIS) <input type="file" id="seed-file" accept=".csv,.ris,.txt"></label>
          <span class="muted small" id="seed-msg" role="status"></span>
        </div>
      </div>
      <div class="row">
        <button class="secondary small-btn" id="seed-resolve">Resolve on OpenAlex / Crossref</button><span class="send-note" data-send="seed_resolve" hidden></span>
        <button class="secondary small-btn" id="seed-split">Suggest a split</button>
        <label class="inline">development share <input id="seed-share" type="number" step="0.01" min="0" max="1" value="${d.dev_share}" style="width:80px"></label>
        <span class="muted small" id="seed-split-msg" role="status"></span>
      </div>
      <div id="seed-split-box" class="small" hidden></div>
      <div id="seed-lookups" class="card"></div>
      ${rows.length ? `<table><tr><th>id</th><th>set</th><th>source</th><th>title</th><th>keywords</th><th>doi</th><th>year</th><th>status</th><th>abstract</th><th>full text</th><th></th></tr>
      ${rows.map(r => `<tr data-seed-row="${esc(r.benchmark_id)}">
        <td>${esc(r.benchmark_id)}</td>
        <td><select data-assign="${esc(r.benchmark_id)}" aria-label="set of ${esc(r.benchmark_id)}">${opt(["Development", "Validation"], r.set_name, ["Dev", "Val"])}</select></td>
        <td><select data-source="${esc(r.benchmark_id)}" aria-label="source of ${esc(r.benchmark_id)}">${sourceOpts(d.sources, r.source)}</select></td>
        <td>${esc(r.title || "")}${r.duplicate_of ? " " + badge("duplicate of " + r.duplicate_of, "bad") : ""}
          ${(r.notices || []).length ? `<ul class="warnings small seed-notices">${r.notices.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}</td>
        <td>${keywordsCell(r)}</td>
        <td class="small">${esc(r.doi || "")}</td><td>${esc(r.year || "")}</td>
        <td>${status(r)}${r.resolve_note ? `<div class="muted small">${esc(r.resolve_note)}</div>` : ""}</td>
        <td class="small">${r.abstract_words ? `${r.abstract_words} w` : `<span class="muted">missing</span>`}</td>
        <td class="small">${fullTextCell(r, files[r.benchmark_id])}</td>
        <td><button class="secondary small-btn" data-seed-remove="${esc(r.benchmark_id)}" aria-label="Remove seed paper ${esc(r.benchmark_id)}">Remove</button></td></tr>`).join("")}</table>`
      : `<p>No seed papers yet.</p>`}`;
    if (window.QWindow) QWindow.remark();   // the seed of the question in the window keeps its rose bar (D7)
    renderLookupsCard(q("#seed-lookups"));   // resolve sends DOIs and titles out: consent state and log next to it
    const msg = q("#seed-msg"), smsg = q("#seed-split-msg");
    q("#seed-add").onclick = wrap(async () => {
      const text = q("#seed-text").value.trim();
      if (!text) { flash(msg, "nothing to add"); return; }
      const r = await post("/api/benchmark/add", { text, set: q("#seed-set").value, source: q("#seed-source").value });
      await loadSeed(r.notices); flash(q("#seed-msg"), `${r.added.length} added${skippedText(r)}${warned(r.notices)}`);
    }, msg);
    q("#seed-file").onchange = wrap(async () => {
      const f = q("#seed-file").files[0]; if (!f) return;
      const text = await f.text();
      const r = await post("/api/benchmark/add", { text, filename: f.name, set: q("#seed-set").value, source: q("#seed-source").value });
      await loadSeed(r.notices); flash(q("#seed-msg"), `${r.added.length} added from ${f.name}${skippedText(r)}${warned(r.notices)}`);
    }, msg);
    q("#seed-resolve").onclick = wrap(async () => {
      flash(msg, "resolving…");
      const r = await post("/api/benchmark/resolve");
      const entry = Object.fromEntries((r.rows || []).filter(x => (x.notices || []).length).map(x => [x.benchmark_id, x.notices]));
      await loadSeed(entry); flash(q("#seed-msg"), r.summary);
    }, msg);
    q("#seed-split").onclick = wrap(async () => {
      const sb = q("#seed-split-box"), raw = q("#seed-share").value.trim();
      sb.hidden = true; sb.innerHTML = ""; lastSplit = null;   // an earlier suggestion never stays beside a refusal
      if (!raw || !Number.isFinite(Number(raw))) throw new Error("the development share must be a number from 0 to 1, e.g. 0.6");
      lastSplit = await post("/api/benchmark/split", { dev_share: Number(raw) });
      sb.hidden = false;
      sb.innerHTML = `<p>${esc(lastSplit.rule)}</p><p><b>Development (${lastSplit.development.length}):</b> ${esc(lastSplit.development.join(", "))}<br>
        <b>Validation (${lastSplit.validation.length}):</b> ${esc(lastSplit.validation.join(", "))}<br>
        <span class="muted">seed ${lastSplit.seed}, share ${lastSplit.dev_share}; nothing changes until you apply.</span></p>
        <button class="secondary small-btn" id="seed-apply">Apply this split</button>`;
      q("#seed-apply").onclick = wrap(async () => {
        const r = await confirmedPost("/api/benchmark/split", { dev_share: lastSplit.dev_share, seed: lastSplit.seed, apply: true });
        await loadSeed(); flash(q("#seed-split-msg"), r ? "split applied" : "Nothing was moved.");
      }, smsg);
    }, smsg);
    box.querySelectorAll("[data-assign]").forEach(sel => sel.onchange = wrap(async () => {
      const id = sel.dataset.assign;
      const r = await confirmedPost("/api/benchmark/assign", { benchmark_id: id, set: sel.value });
      await loadSeed();   // a cancelled move redraws the select with the set it still has
      if (!r) flash(q("#seed-msg"), `Nothing was moved: ${id} stays in Validation.`);
      const again = q(`[data-assign="${CSS.escape(id)}"]`); if (again) again.focus();
    }, msg));
    if (d.n_used) q("#seed-move-used").onclick = wrap(async () => {
      const r = await confirmedPost("/api/benchmark/move-used", {});
      await loadSeed();
      flash(q("#seed-msg"), r ? `Moved to Development: ${r.moved.join(", ")}.` : "Nothing was moved.");
      const back = q(r ? "#seed-invite" : "#seed-move-used"); if (back) back.focus();   // the button is gone after a move
    }, msg);
    box.querySelectorAll("[data-keywords]").forEach(inp => inp.onchange = wrap(async () => {
      await post("/api/benchmark/keywords", { benchmark_id: inp.dataset.keywords, keywords: inp.value }); await loadSeed();
    }, msg));
    box.querySelectorAll("[data-source]").forEach(sel => sel.onchange = wrap(async () => {
      await post("/api/benchmark/source", { benchmark_id: sel.dataset.source, source: sel.value }); await loadSeed();
    }, msg));
    box.querySelectorAll("[data-pdf-add]").forEach(inp => inp.onchange = wrap(async () => {
      const f = inp.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      const r = await call(`/api/seeds/files/${encodeURIComponent(inp.dataset.pdfAdd)}`, { method: "POST", body: fd });
      await loadSeed(); flash(q("#seed-msg"), `${inp.dataset.pdfAdd}: PDF kept in ${r.file} (stays on this computer)`);
    }, msg));
    box.querySelectorAll("[data-pdf-remove]").forEach(b => b.onclick = wrap(async () => {
      if (!(await askConfirm(`Delete the project's copy of ${b.dataset.pdfRemove}'s PDF and its extracted text? Your original file is untouched.`, "Delete the copy"))) return;
      await del(`/api/seeds/files/${encodeURIComponent(b.dataset.pdfRemove)}`); await loadSeed();
    }, msg));
    box.querySelectorAll("[data-seed-remove]").forEach(b => b.onclick = wrap(async () => {
      if (!(await askConfirm(`Remove ${b.dataset.seedRemove} from the seed papers?`, "Remove"))) return;
      await del(`/api/benchmark/${encodeURIComponent(b.dataset.seedRemove)}`); await loadSeed();
    }, msg));
  }

  // ------------------------------------------------------------------ find more seed papers (optional task, D4)
  // reference-list mining and citation candidates, with the online-lookup consent block beside them
  async function loadMore() {
    if (!q("#seed-chain")) return;
    const d = await call("/api/benchmark/seed");
    renderReferences(d.rows.filter(r => r.set_name === "Development").map(r => r.benchmark_id));
    renderLookupsCard(q("#more-lookups"));
    await loadCandidates().catch(e => { const c = q("#seed-chain"); if (c) flash(c, e.message, true); });
  }

  // Reference lists of the development seeds (papers beyond the query, D26): OpenAlex first, the seed's PDF as the
  // fallback, and the entries one has and the other lacks. Optional tiers parse the PDF entries further. Validation
  // seeds are not offered: their reference lists are not read or sent.
  function renderReferences(devIds) {
    const box = q("#seed-refs"); if (!box) return;
    box.innerHTML = `<h3>Reference lists of the development seeds</h3>
      <p class="small">OpenAlex's list first (it sends the seed's DOI, under online lookups); the seed's PDF fills the gap and
        sends nothing. A reference DOI OpenAlex did not list becomes a candidate with origin pdf. A validation paper's reference
        list is not mined, sent or counted; move it to Development to use it.</p>
      <div class="row">
        <label class="inline">seed <select id="refs-seed">${opt(["", ...devIds], "", ["every development seed", ...devIds])}</select></label>
        <label class="inline">route <select id="refs-route">${opt(["auto", "openalex", "pdf"], "auto",
          ["OpenAlex when lookups are on, and the PDF", "OpenAlex only", "PDF only (sends nothing)"])}</select></label>
        <label class="inline">parse the PDF entries with <select id="refs-tier">${opt(["", "grobid", "crossref", "model"], "",
          ["nothing more", "GROBID on this computer (whole PDF)", "Crossref (titles only, needs online lookups)", "the construct model (keeps only what the entry says)"])}</select></label>
        <button class="secondary small-btn" id="refs-run"${devIds.length ? "" : " disabled"}>Compare reference lists</button>
        <span class="small" id="refs-msg" role="status"></span>
      </div>
      <p class="send-note" data-send="seed_refs" hidden></p><p class="send-note" data-send="ref_match" hidden></p>
      <div id="refs-out" class="small"></div>`;
    const rmsg = q("#refs-msg");
    q("#refs-run").onclick = wrap(async () => {
      flash(rmsg, "reading the reference lists…");
      const r = await post("/api/benchmark/references", { benchmark_id: q("#refs-seed").value || null,
        route: q("#refs-route").value, tier: q("#refs-tier").value || null });
      flash(rmsg, "");
      q("#refs-out").innerHTML = `<ul>${(r.lines || []).map(l => `<li>${esc(l)}</li>`).join("")}</ul>`;
      if ((r.seeds || []).some(x => x.new_candidates)) await loadCandidates();
    }, rmsg);
  }

  // Candidates from the seed papers (change 2c, D16): the works the development seeds cite and the works citing them,
  // fetched from OpenAlex under the online-lookup consent. Nothing becomes a seed paper until Add is clicked.
  // Papers beyond the query (D23): ranked by links to the seeds, snowball rounds from the added papers, and exports of
  // any other tool imported as candidates (nothing sent; an import can be dismissed as a whole).
  const LINK_LABELS = { references: "a seed cites it", cited_by: "it cites a seed", both: "both ways" };
  const roundLabel = (k) => k === 0 ? "0 (from a file)" : k === 1 ? "1 (from the seeds)" : `${k} (snowball)`;
  const candStatus = (c) => c.status === "added" ? badge(`added as ${c.benchmark_id}`, "ok")
    : c.status === "dismissed" ? `${badge("dismissed")}<div class="muted">${esc(c.by || "")} ${esc(fmtWhen(c.at))}</div>`
    : badge("new");
  let candRound = "";   // the round filter of the list ("" = every round)
  async function loadCandidates(note) {
    const box = q("#seed-chain"); if (!box) return;
    const d = await call("/api/benchmark/candidates" + (candRound !== "" ? `?round=${encodeURIComponent(candRound)}` : ""));
    const rows = d.candidates || [], rounds = d.rounds || [], imports = d.imports || [];
    box.innerHTML = `<h3>Candidates from seed papers and other tools</h3>
      <p class="muted small">The works your development seed papers cite and the works citing them, from OpenAlex. This sends
        the development seeds' DOIs (it needs online lookups); validation seeds are never sent. Nothing is added until you choose.</p>
      <div class="row">
        <label class="inline">direction <select id="chain-dir">${opt(["both", "references", "cited_by"], "both",
          ["both", "references (works the seeds cite)", "cited by (works citing the seeds)"])}</select></label>
        <button class="secondary small-btn" id="chain-run">Find candidates</button>
        <button class="secondary small-btn" id="chain-again" title="Chains only the development seeds you added from candidates, in the directions not chained yet">Chain again from added papers</button>
        <span class="muted small" id="chain-msg" role="status"></span><span class="send-note" data-send="seed_chain" hidden></span>
      </div>
      <div class="row">
        <label class="inline">file from another tool <input type="file" id="cand-file" accept=".ris,.csv,.tsv,.txt,.bib,.xls,.xlsx"></label>
        <label class="inline">origin <input id="cand-origin" placeholder="e.g. citationchaser" size="16"></label>
        <button class="secondary small-btn" id="cand-import">Import candidates</button>
        <span class="small">Nothing is sent: the file is read on this computer.</span>
      </div>
      ${imports.length ? `<ul class="small" id="cand-imports">${imports.map(i => `<li>import ${esc(i.import_no)}: ${esc(i.origin)} (${esc(i.file_name || "")}),
        ${esc(i.n_new)} new, ${esc(i.still_new)} still new · ${esc(i.by || "")} ${esc(fmtWhen(i.at))}
        ${i.still_new ? `<button class="secondary small-btn" data-import-dismiss="${esc(i.import_no)}" aria-label="Dismiss import ${esc(i.import_no)}">Dismiss this import</button>` : ""}</li>`).join("")}</ul>` : ""}
      ${d.stop_notice ? `<p id="cand-stop"><b>${esc(d.stop_notice)}</b> You can still chain another round.</p>` : ""}
      ${rounds.length > 1 || candRound !== "" ? `<label class="inline small">round <select id="cand-round">${opt(["", ...rounds.map(r => String(r.round))], candRound,
          ["every round", ...rounds.map(r => `${roundLabel(r.round)}: ${r.n} candidates, ${r.added} added`)])}</select></label>` : ""}
      ${rows.length ? `<p class="small">${esc(d.rank_note || "")}</p>
      <table><tr><th>rank</th><th>#</th><th>title</th><th>linked to</th><th>round</th><th>origin</th><th>year</th><th>doi</th><th>status</th><th></th></tr>
      ${rows.map(c => `<tr${c.status === "dismissed" ? ' class="muted"' : ""}>
        <td>${esc(c.rank ?? "")}</td><td>${esc(c.candidate_id)}</td>
        <td>${esc(c.title || "")}${c.retrieved ? " " + badge("retrieved by the query") : ""}${c.good_validation ? " " + badge("good validation candidate", "ok") : ""}</td>
        <td class="small">${esc((c.linking_seeds || []).join(", "))}<div class="muted">${esc(LINK_LABELS[c.direction] || c.direction || "")}</div></td>
        <td class="small">${esc(c.chain_round ?? "")}</td><td class="small">${esc((c.origins || []).join(", "))}</td>
        <td>${esc(c.year || "")}</td><td class="small">${esc(c.doi || "")}</td>
        <td class="small">${candStatus(c)}</td>
        <td>${c.status === "added" ? "" : `
          <button class="secondary small-btn" data-cand-add="${esc(c.candidate_id)}" data-set="Development" aria-label="Add candidate ${esc(c.candidate_id)} to the Development seed papers">Add to Development seed papers</button>
          <button class="secondary small-btn" data-cand-add="${esc(c.candidate_id)}" data-set="Validation" aria-label="Add candidate ${esc(c.candidate_id)} to the Validation seed papers">Add to Validation seed papers</button>
          ${c.status === "new" ? `<button class="secondary small-btn" data-cand-dismiss="${esc(c.candidate_id)}" aria-label="Dismiss candidate ${esc(c.candidate_id)}">Dismiss</button>` : ""}`}</td></tr>`).join("")}</table>`
      : `<p class="muted small">No candidates${candRound !== "" ? " in this round" : " yet"}.</p>`}`;
    const cmsg = q("#chain-msg");
    if (note) flash(cmsg, note);
    const chainNote = (r) => [`${r.round ? `round ${r.round}: ` : ""}${r.seeds_chained} development seed(s) chained: ${r.found} works, ${r.new} new`,
      ...(r.messages || [])].join("; ");
    q("#chain-run").onclick = wrap(async () => {
      flash(cmsg, "asking OpenAlex…");
      await loadCandidates(chainNote(await post("/api/benchmark/chain", { direction: q("#chain-dir").value })));
    }, cmsg);
    q("#chain-again").onclick = wrap(async () => {
      flash(cmsg, "asking OpenAlex…");
      await loadCandidates(chainNote(await post("/api/benchmark/chain", { direction: q("#chain-dir").value, from_candidates: true })));
    }, cmsg);
    q("#cand-import").onclick = wrap(async () => {
      const f = q("#cand-file").files[0], origin = q("#cand-origin").value.trim();
      if (!f || !origin) { flash(cmsg, "choose a file and say where it came from (origin)", true); return; }
      const fd = new FormData();
      fd.append("file", f);
      fd.append("origin", origin);
      const r = await call("/api/benchmark/candidates/import", { method: "POST", body: fd }).then(changed);
      await loadCandidates([`${r.import_no ? `import ${r.import_no}: ` : ""}${r.n_records} record(s) from ${r.file}: ${r.new} new, ${r.merged} known candidate(s) gained the origin; nothing was sent`,
        ...(r.messages || [])].join("; "));
    }, cmsg);
    const rsel = q("#cand-round");
    if (rsel) rsel.onchange = wrap(async () => { candRound = rsel.value; await loadCandidates(); }, cmsg);
    box.querySelectorAll("[data-import-dismiss]").forEach(b => b.onclick = wrap(async () => {
      if (b.disabled) return;   // a double click must not overwrite the result with "0 dismissed"
      b.disabled = true;
      let r;
      try { r = await post(`/api/benchmark/candidates/imports/${encodeURIComponent(b.dataset.importDismiss)}/dismiss`); }
      catch (e) { b.disabled = false; throw e; }
      await loadCandidates(`import ${r.import_no}: ${r.dismissed} candidate(s) dismissed (Undo on the History page puts them back)`);
    }, cmsg));
    box.querySelectorAll("[data-cand-add]").forEach(b => b.onclick = wrap(async () => {
      const r = await post(`/api/benchmark/candidates/${encodeURIComponent(b.dataset.candAdd)}/add`, { set: b.dataset.set });
      await loadCandidates(`${r.added.benchmark_id} added to ${r.added.set} from candidate ${b.dataset.candAdd}${warned(r.notices)}`);
    }, cmsg));
    box.querySelectorAll("[data-cand-dismiss]").forEach(b => b.onclick = wrap(async () => {
      await post(`/api/benchmark/candidates/${encodeURIComponent(b.dataset.candDismiss)}/dismiss`);
      await loadCandidates(`candidate ${b.dataset.candDismiss} dismissed (kept, listed last)`);
    }, cmsg));
  }

  // ------------------------------------------------------------------ databases
  async function loadDatabases() {
    const box = q("#setup-databases"); if (!box) return;
    const d = await call("/api/platforms");
    const chosenKeys = new Set(d.chosen.map(c => c.key));
    const available = d.catalogue.filter(c => !chosenKeys.has(c.key));
    box.innerHTML = `
      <h2>Databases</h2>
      <p class="muted">Search these in this order. The query is developed on the <b>development</b> platform and validated once on the others.</p>
      ${d.chosen.length ? `<table><tr><th>#</th><th>platform</th><th>role</th><th>coverage</th><th>export</th><th></th></tr>
      ${d.chosen.map((c, i) => `<tr>
        <td>${i + 1}</td>
        <td><b>${esc(c.label)}</b> <span class="muted small">${esc(c.key)}${c.custom ? ` · custom, ${esc(c.syntax)} syntax` : ""}</span></td>
        <td><select data-role="${esc(c.key)}" aria-label="Role of ${esc(c.label)}">${opt(["development", "validation"], c.role)}</select></td>
        <td class="small">${esc(c.coverage || "")}</td>
        <td class="small">${esc((c.export_formats || []).join(", "))}${c.export_cap ? `<div class="muted">cap ${esc(c.cap_text || c.export_cap)}: ${esc(c.cap_note || "")}</div>` : ""}</td>
        <td class="nowrap"><button class="secondary small-btn" data-move="${esc(c.key)}" data-dir="-1" aria-label="Move ${esc(c.label)} up" ${i === 0 ? "disabled" : ""}>Up</button>
          <button class="secondary small-btn" data-move="${esc(c.key)}" data-dir="1" aria-label="Move ${esc(c.label)} down" ${i === d.chosen.length - 1 ? "disabled" : ""}>Down</button>
          <button class="secondary small-btn" data-plat-remove="${esc(c.key)}" aria-label="Remove ${esc(c.label)}">Remove</button></td></tr>`).join("")}</table>`
      : `<p class="muted">No platform chosen yet.</p>`}
      <div class="row">
        <label class="inline">add from the catalogue <select id="plat-pick">${opt(available.map(a => a.key), "", available.map(a => a.label))}</select></label>
        <button class="secondary small-btn" id="plat-add" ${available.length ? "" : "disabled"}>Add</button>
        <span class="muted small" id="plat-msg" role="status"></span>
      </div>
      <details><summary>Catalogue details, or add a platform that is not listed</summary>
        <table>${d.catalogue.map(c => `<tr><td><b>${esc(c.label)}</b><div class="muted small">${esc(c.key)}</div></td>
          <td class="small">${esc(c.description || "")}<div class="muted">${esc(c.coverage || "")}</div></td>
          <td class="small">${esc((c.export_formats || []).join(", "))}<div class="muted">${c.export_cap ? `cap ${esc(c.cap_text || c.export_cap)}` : "no practical cap"}: ${esc(c.cap_note || "")}</div></td></tr>`).join("")}</table>
        <form id="plat-custom" class="row" style="margin-top:10px">
          <label class="inline">key <input name="key" required placeholder="library_host" style="width:140px"></label>
          <label class="inline">label <input name="label" placeholder="Our library host" style="width:180px"></label>
          <label class="inline">renders like <select name="syntax">${opt(d.families)}</select></label>
          <button type="submit" class="secondary small-btn">Add custom platform</button>
        </form>
      </details>`;
    const msg = q("#plat-msg");
    q("#plat-add").onclick = wrap(async () => { await post("/api/platforms/add", { key: q("#plat-pick").value }); await loadDatabases(); }, msg);
    q("#plat-custom").onsubmit = wrap(async (ev) => {
      ev.preventDefault(); const f = ev.target;
      await post("/api/platforms/add", { key: f.key.value.trim(), label: f.label.value.trim() || null, syntax: f.syntax.value });
      await loadDatabases();
    }, msg);
    box.querySelectorAll("[data-role]").forEach(sel => sel.onchange = wrap(async () => {
      await post("/api/platforms/role", { key: sel.dataset.role, role: sel.value }); await loadDatabases();
    }, msg));
    box.querySelectorAll("[data-move]").forEach(b => b.onclick = wrap(async () => {
      const keys = d.chosen.map(c => c.key), i = keys.indexOf(b.dataset.move), j = i + Number(b.dataset.dir);
      if (j < 0 || j >= keys.length) return;
      [keys[i], keys[j]] = [keys[j], keys[i]];
      await post("/api/platforms/order", { keys }); await loadDatabases();
      focusMoved(q("#setup-databases"), b.dataset.move, b.dataset.dir);
    }, msg));
    box.querySelectorAll("[data-plat-remove]").forEach(b => b.onclick = wrap(async () => {
      const keys = d.chosen.map(c => c.key), i = keys.indexOf(b.dataset.platRemove);
      await del(`/api/platforms/${encodeURIComponent(b.dataset.platRemove)}`); await loadDatabases();
      focusAfterRemove(q("#setup-databases"), keys.filter(k => k !== b.dataset.platRemove), i);
    }, msg));
  }

  // the table is drawn again after a move: keep the keyboard on the moved row, on the same button unless it is now
  // disabled (the row reached the top or the bottom), then on the row's other button
  function focusMoved(box, key, dir) {
    if (!box) return;
    const btns = [...box.querySelectorAll("[data-move]")].filter(x => x.dataset.move === key);
    const same = btns.find(x => x.dataset.dir === dir && !x.disabled), other = btns.find(x => !x.disabled);
    if (same || other) (same || other).focus();
  }

  // the removed row is gone: keep the keyboard in the table, on the Remove button of the row that took its place (or
  // of the new last row), and on the catalogue picker once no platform is left
  function focusAfterRemove(box, keys, i) {
    if (!box) return;
    const key = keys[Math.min(i, keys.length - 1)];
    const b = key === undefined ? null : [...box.querySelectorAll("[data-plat-remove]")].find(x => x.dataset.platRemove === key);
    const t = b || box.querySelector("#plat-pick");
    if (t) t.focus();
  }

  // ------------------------------------------------------------------ advanced: review protocol
  const openSections = new Set();
  function valueInput(r) {
    const t = typeof r.default, id = `proto-${r.path.replace(/\./g, "-")}`;
    if (t === "boolean") return `<select id="${id}" aria-label="${esc(r.path)}" data-type="boolean">${opt(["true", "false"], String(r.value))}</select>`;
    // a text field, so "x" or an empty field reaches the server as typed and is refused in plain words, never sent as 0
    if (t === "number") return `<input id="${id}" aria-label="${esc(r.path)}" data-type="number" type="text" inputmode="decimal" value="${esc(r.value)}">`;
    if (t === "string" || r.default == null) return `<input id="${id}" aria-label="${esc(r.path)}" data-type="string" value="${esc(r.value == null ? "" : r.value)}">`;
    return `<input id="${id}" aria-label="${esc(r.path)}" data-type="json" value="${esc(JSON.stringify(r.value))}">`;
  }
  function parseValue(el) {
    const v = el.value.trim();
    if (el.dataset.type === "boolean") return v === "true";
    if (el.dataset.type === "number") return v !== "" && Number.isFinite(Number(v)) ? Number(v) : v;
    if (el.dataset.type === "json") return JSON.parse(v);
    return v === "" ? null : v;
  }
  // the Advanced protocol card on Settings (shell.js::loadSettings calls window.loadProtocol)
  async function loadProtocol() {
    const box = q("#protocol-editor"); if (!box) return;
    const d = await call("/api/protocol");
    const wasOpen = q("#protocol-details", box) ? q("#protocol-details", box).open : false;
    box.innerHTML = `
      <details id="protocol-details" ${wasOpen ? "open" : ""}>
        <summary><b>Advanced: review protocol</b> <span class="muted small">${d.changed ? `${d.changed} setting${d.changed === 1 ? "" : "s"} changed from the defaults` : "all defaults"} · hash ${esc(d.hash.slice(0, 12))}</span></summary>
        <p class="muted small">Every methodological setting with its default. The defaults reproduce the original review; change a value only when you can explain it in the methods section.
          <button class="secondary small-btn" id="proto-reset-all">Reset everything to the defaults</button> <span class="muted small" id="proto-msg" role="status"></span></p>
        ${d.sections.map(s => `<details class="proto-section" data-section="${esc(s.key)}" ${openSections.has(s.key) ? "open" : ""}>
          <summary>${esc(s.key)}${s.changed ? " " + badge(`${s.changed} changed`, "warn") : ""} <span class="muted small">${esc(s.doc)}</span></summary>
          <table class="proto-table">
          ${d.rows.filter(r => r.section === s.key).map(r => `<tr class="${r.changed ? "changed" : ""}">
            <td><code>${esc(r.key)}</code>${r.changed ? " " + badge("changed", "warn") : ""}<div class="muted small">${esc(r.description)}</div></td>
            <td class="proto-value">${valueInput(r)}</td>
            <td class="small muted">default: <code>${esc(JSON.stringify(r.default))}</code></td>
            <td class="nowrap"><button class="secondary small-btn" data-proto-set="${esc(r.path)}" aria-label="Set ${esc(r.path)}">Set</button>
              ${r.changed ? `<button class="secondary small-btn" data-proto-reset="${esc(r.path)}" aria-label="Reset ${esc(r.path)} to the default">Reset</button>` : ""}</td></tr>`).join("")}
          </table></details>`).join("")}
        ${cmdFold("<code>wider protocol explain</code> prints the same list.")}
      </details>`;
    const msg = q("#proto-msg");
    box.querySelectorAll(".proto-section").forEach(s => s.ontoggle = () => { if (s.open) openSections.add(s.dataset.section); else openSections.delete(s.dataset.section); });
    box.querySelectorAll("[data-proto-set]").forEach(b => b.onclick = wrap(async () => {
      const path = b.dataset.protoSet, el = q(`#proto-${path.replace(/\./g, "-")}`, box);
      let r;
      try { r = await post("/api/protocol/set", { path, value: parseValue(el) }); }
      catch (e) { showStored(el); throw e; }   // a refused value does not stay in the field as if it were saved
      await loadProtocol(); focusSet(path); flash(q("#proto-msg"), `${path} set` + (r && r.notice ? `; ${r.notice}` : ""));
    }, msg));
    // Enter in a setting's text field equals its Set button (the focus then stays on Set)
    box.querySelectorAll(".proto-value input").forEach(el => el.onkeydown = (e) => {
      if (e.key !== "Enter" || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
      const b = el.closest("tr").querySelector("[data-proto-set]");
      e.preventDefault(); b.focus(); b.click();
    });
    box.querySelectorAll("[data-proto-reset]").forEach(b => b.onclick = wrap(async () => {
      const path = b.dataset.protoReset;
      const r = await post("/api/protocol/reset", { path }); await loadProtocol(); focusSet(path);
      if (r && r.notice) flash(q("#proto-msg"), r.notice);
    }, msg));
    q("#proto-reset-all").onclick = wrap(async () => {
      if (!(await askConfirm("Reset every protocol setting to the original review's value?", "Reset"))) return;
      const r = await post("/api/protocol/reset", {}); await loadProtocol(); q("#proto-reset-all").focus();
      if (r && r.notice) flash(q("#proto-msg"), r.notice);
    }, msg);
  }
  // the redrawn table replaced the pressed button: the keyboard goes back to the row's Set button
  function focusSet(path) { const b = q(`#protocol-editor [data-proto-set="${path}"]`); if (b) b.focus(); }
  function showStored(el) {
    if (el.tagName === "SELECT") { const o = [...el.options].find(x => x.defaultSelected); if (o) el.value = o.value; }
    else el.value = el.defaultValue;
  }


  // ------------------------------------------------------------------ prior reviews (local by default)
  const POLICY_TEXT = "These files stay on this computer: they are parsed and matched locally and are never uploaded or sent to any model or service. The two switches below are off unless you turn them on.";
  async function loadPrior() {
    const box = q("#setup-prior"); if (!box) return;
    const d = await call("/api/prior-reviews");
    const pol = d.policy, rows = d.reviews;
    const kind = (k) => ({ list: "reference export", pdf: "PDF", text: "pasted" }[k] || k);
    const lookupOff = !pol.allow_openalex_lookup;
    const dis = lookupOff ? ' disabled title="turn on the OpenAlex lookup switch first"' : "";
    box.innerHTML = `
      <h2>Prior reviews</h2>
      <div class="policy-box" id="prior-policy">
        <p><b>Data policy.</b> ${esc(POLICY_TEXT)}</p>
        <label class="inline"><input type="checkbox" id="prior-allow-lookup"${pol.allow_openalex_lookup ? " checked" : ""}>
          Allow OpenAlex lookup: send the DOIs and titles of these papers to OpenAlex to resolve identifiers and chain citations</label><br>
        <label class="inline"><input type="checkbox" id="prior-allow-excerpts"${pol.allow_llm_excerpts ? " checked" : ""}>
          Allow excerpts in prompts: let the titles of these papers appear in construct proposal prompts sent to your AI model</label>
        <span class="muted small" id="prior-policy-msg" role="status"></span>
      </div>
      <p class="muted"><b>${rows.length}</b> review${rows.length === 1 ? "" : "s"}, <b>${d.n_papers}</b> papers (${d.n_doi} with a DOI).
        ${pol.none_known ? badge("no prior review known", "ok") : ""}</p>
      <div class="form-grid">
        <label>Paste DOIs, titles or reference strings, one per line
          <textarea id="prior-text" rows="3" placeholder="10.1000/example.123&#10;A title of an included study&#10;Author, A. (2019). Title. Journal, 1(2), 3-4."></textarea></label>
        <div class="row">
          <label class="inline">label <input id="prior-label" placeholder="e.g. Smith 2019 review" style="width:180px"></label>
          <button id="prior-add" class="secondary small-btn">Add pasted list</button>
          <label class="inline">or a file (RIS, CSV, XLSX, BibTeX, TXT, or the review's PDF) <input type="file" id="prior-file" accept=".ris,.csv,.tsv,.xlsx,.xls,.bib,.txt,.pdf"></label>
          <button id="prior-none" class="secondary small-btn">${pol.none_known ? "I do know of some" : "I know of none"}</button>
          <span class="muted small" id="prior-msg" role="status"></span>
        </div>
      </div>
      <div class="row">
        <button class="secondary small-btn" id="prior-resolve"${dis}>Resolve on OpenAlex</button>
        <label class="inline small">direction <select id="prior-setup-chain-dir"${dis}><option value="both">references and citing works</option><option value="backward">references only</option><option value="forward">citing works only</option></select></label>
        <button class="secondary small-btn" id="prior-chain"${dis}>Chain citations</button>
        <span class="muted small" id="prior-act-msg" role="status">${lookupOff ? "lookups stay off until you turn on the switch above" : ""}</span>
      </div>
      <p class="send-note" data-send="prior_resolve" hidden></p><p class="send-note" data-send="prior_chain" hidden></p>
      ${rows.length ? `<table><tr><th>id</th><th>label</th><th>kind</th><th>file</th><th>papers</th><th>with DOI</th><th>resolved</th><th>found in exports</th><th></th></tr>
      ${rows.map(r => `<tr>
        <td>${r.review_id}</td><td><b>${esc(r.label)}</b>${r.note ? `<div class="muted small">${esc(r.note)}</div>` : ""}</td>
        <td>${esc(kind(r.kind))}</td><td class="small">${esc(r.file_name || "")}</td>
        <td>${r.n_papers}</td><td>${r.n_doi}</td><td>${r.n_resolved}</td><td>${r.n_matched}</td>
        <td class="nowrap"><button class="secondary small-btn" data-prior-papers="${r.review_id}" aria-label="Papers of prior review ${esc(r.label)}">Papers</button>
          <button class="secondary small-btn" data-prior-remove="${r.review_id}" aria-label="Remove prior review ${esc(r.label)}">Remove</button></td></tr>`).join("")}</table>
      <div id="prior-papers" class="small" hidden></div>` : `<p class="muted">No prior reviews added yet.</p>`}`;
    const msg = q("#prior-msg"), pmsg = q("#prior-policy-msg"), amsg = q("#prior-act-msg");
    const patchPolicy = (body) => call("/api/project", { method: "PATCH", body: JSON.stringify({ prior_reviews: body }) }).then(changed);
    q("#prior-allow-lookup").onchange = wrap(async (e) => { await patchPolicy({ allow_openalex_lookup: e.target.checked }); await loadPrior(); flash(q("#prior-policy-msg"), "saved"); }, pmsg);
    q("#prior-allow-excerpts").onchange = wrap(async (e) => { await patchPolicy({ allow_llm_excerpts: e.target.checked }); await loadPrior(); flash(q("#prior-policy-msg"), "saved"); }, pmsg);
    q("#prior-none").onclick = wrap(async () => { await patchPolicy({ none_known: !pol.none_known }); await loadPrior(); }, msg);
    q("#prior-add").onclick = wrap(async () => {
      const text = q("#prior-text").value.trim();
      if (!text) { flash(msg, "nothing to add"); return; }
      const r = await post("/api/prior-reviews/text", { text, label: q("#prior-label").value.trim() || "pasted list" });
      await loadPrior(); flash(q("#prior-msg"), `${r.n_papers} papers added${r.warnings && r.warnings.length ? "; " + r.warnings.join("; ") : ""}`);
    }, msg);
    q("#prior-file").onchange = wrap(async () => {
      const f = q("#prior-file").files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f); fd.append("label", q("#prior-label").value.trim());
      flash(msg, "parsing locally…");
      const r = await fetch("/api/prior-reviews/upload", { method: "POST", body: fd });
      if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) { /* no body */ } throw new Error(m); }
      const res = await r.json(); changed(res);
      await loadPrior(); flash(q("#prior-msg"), `${res.n_papers} papers from ${f.name}${res.warnings && res.warnings.length ? "; " + res.warnings.join("; ") : ""}`);
    }, msg);
    q("#prior-resolve").onclick = wrap(async () => {
      flash(amsg, "resolving on OpenAlex…");
      const r = await post("/api/prior-reviews/resolve");
      await loadPrior(); flash(q("#prior-act-msg"), `${r.resolved} resolved, ${r.unresolved} unresolved`);
    }, amsg);
    q("#prior-chain").onclick = wrap(async () => {
      flash(amsg, "chaining citations on OpenAlex…");
      const r = await post(`/api/prior-reviews/chain?direction=${encodeURIComponent(q("#prior-setup-chain-dir").value)}`);
      await loadPrior(); flash(q("#prior-act-msg"), r.file ? `${r.n_new} new records; ${r.n_exported} written to ${r.file} (import them on the Search results page)` : `${r.n_new} new records; nothing new to export`);
      if (window.showStageResult && r.stage_result) showStageResult(r.stage_result);
    }, amsg);
    box.querySelectorAll("[data-prior-remove]").forEach(b => b.onclick = wrap(async () => { await del(`/api/prior-reviews/${b.dataset.priorRemove}`); await loadPrior(); }, msg));
    box.querySelectorAll("[data-prior-papers]").forEach(b => b.onclick = wrap(async () => {
      const r = await call(`/api/prior-reviews/${b.dataset.priorPapers}/papers`);
      const pb = q("#prior-papers"); pb.hidden = false;
      pb.innerHTML = `<p><b>${esc(r.review.label)}</b>: ${r.papers.length} papers (confidence: doi = printed DOI, parsed = title after the year, heuristic = check the raw entry)</p>
        <table><tr><th>#</th><th>confidence</th><th>year</th><th>doi</th><th>title</th></tr>
        ${r.papers.map(x => `<tr><td>${esc(x.position)}</td><td>${badge(x.confidence, x.confidence === "heuristic" ? "bad" : x.confidence === "doi" ? "ok" : "")}</td><td>${esc(x.year || "")}</td>
          <td class="small">${esc(x.doi || "")}</td><td>${esc(x.title || "")}${x.raw_ref && x.confidence !== "doi" ? `<div class="muted small">${esc(x.raw_ref)}</div>` : ""}</td></tr>`).join("")}</table>`;
    }, msg));
  }

  // ------------------------------------------------------------------ the Review plan and its task pages
  const TASKS = ["question", "seeds", "prior", "databases", "more"];
  const TITLES = { question: "Research question", seeds: "Seed papers", prior: "Prior reviews", databases: "Databases",
    more: "Find more seed papers" };
  const STATE_WORDS = { done: "Done", todo: "To do", waiting: "Waiting for you", optional: "Optional" };
  const STATE_ICONS = { done: "✓", todo: "○", waiting: "!", optional: "–" };
  // the task of the address (#setup?task=seeds); an unknown or missing key is the Review plan itself ("")
  function currentTask() {
    const m = /[?&]task=([^&]*)/.exec((typeof location !== "undefined" && location.hash) || "");
    let k = "";
    try { k = m ? decodeURIComponent(m[1]) : ""; } catch (e) { k = ""; }
    return TASKS.includes(k) ? k : "";
  }
  const taskHref = (k) => `#setup?task=${k}`;
  function cardHtml(c) {
    const qs = c.questions ? `<span class="pc-questions">${c.questions === 1 ? "1 question" : `${c.questions} questions`} from WIDER</span>` : "";
    return `<li class="plan-card s-${esc(c.state)}" data-plan-task="${esc(c.key)}"><a href="${taskHref(esc(c.key))}">
      <span class="pc-icon" aria-hidden="true">${STATE_ICONS[c.state] || ""}</span>
      <span class="pc-body"><span class="pc-title">${esc(c.title)}</span>
        <span class="pc-line">${esc(c.line)}</span>${qs}</span>
      <span class="pc-state">${esc(STATE_WORDS[c.state] || c.state)}</span></a></li>`;
  }
  let planSeq = 0;
  async function loadPlan() {
    const n = ++planSeq;
    const d = await call("/api/plan");
    if (n !== planSeq) return d;   // an older reply never draws over a newer one
    const box = q("#plan-cards");
    if (box) box.innerHTML = d.tasks.map(cardHtml).join("");
    const line = q("#plan-summary-line");
    if (line) line.innerHTML = d.ready ? `✓ ${esc(capital(d.summary))}.` : `${esc(capital(d.summary))}.`;
    // the page's one filled button (D32 F15): open the first task that waits for the researcher, else the first to do;
    // a ready plan has none here, as "Continue to ..." then ends the can-do box
    const nx = q("#plan-next");
    if (nx) {
      const t = d.ready ? null : (d.tasks.find(c => c.state === "waiting") || d.tasks.find(c => c.state === "todo"));
      nx.innerHTML = t ? `<a class="btn primary main-act" href="${taskHref(esc(t.key))}">Open ${esc(t.title)}</a>` : "";
      nx.hidden = !t;
    }
    return d;
  }
  const capital = (t) => String(t || "").charAt(0).toUpperCase() + String(t || "").slice(1);
  // the plan or one task page is shown; "Next task: <title>" at the end of each task page but the last
  function showTask(task) {
    const plan = q("#plan-view"); if (plan) plan.hidden = !!task;
    for (const k of TASKS) { const v = q(`#task-${k}`); if (v) v.hidden = k !== task; }
    if (task) {
      const i = TASKS.indexOf(task), next = TASKS[i + 1];
      const nx = q(`[data-next="${task}"]`);
      if (nx) nx.innerHTML = next ? `Next task: <a href="${taskHref(next)}">${esc(TITLES[next])}</a>` : "";
      window.pageTitle = `${TITLES[task]} – Review plan – WIDER`;
      if (window.Jobs && Jobs.retitle) Jobs.retitle(); else document.title = window.pageTitle;
    }
  }
  window.renderSetupCards = async function () {
    const task = currentTask();
    if (!task && typeof location !== "undefined" && /[?&]task=/.test(location.hash || "")) history.replaceState(null, "", "#setup");   // unknown task: the plan
    showTask(task);
    const safe = (fn, sel) => fn().catch(e => { const el = q(sel); if (el) el.innerHTML = `<p class="bad">${esc(e.message)}</p>`; });
    const load = { seeds: [loadSeed, "#setup-seed"], prior: [loadPrior, "#setup-prior"],
      databases: [loadDatabases, "#setup-databases"], more: [loadMore, "#seed-chain"] }[task];
    await Promise.all([safe(loadPlan, "#plan-cards"), load ? safe(load[0], load[1]) : null]);
  };
  window.refreshPlan = () => loadPlan();
  window.currentSetupTask = currentTask;
  window.loadProtocol = loadProtocol;
  window.loadSeed = loadSeed;
  window.loadMore = loadMore;
})();
