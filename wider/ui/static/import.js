// Import page: export sanity table (GET /api/import/check) and file upload (POST /api/import/upload).
// Loaded after app.js; app.js calls loadImportCheck() from loadImport().
const VERDICT_LABEL = { ok: "ok", capped: "capped", off_topic: "off topic", empty: "empty", duplicate: "duplicate" };
// an "ok" file below one threshold says so in its label (wider/ingest/sanity.py verdict_label)
const verdictLabel = (r) => r.verdict === "ok" && r.low ? "ok, one check low" : (VERDICT_LABEL[r.verdict] || r.verdict);
// `esc` comes from common.js (loaded first).

// the files table (ui-redesign D32 F8): status, the file check's verdict and "Enters deduplication" side by side. A file
// the check flags (off topic, empty, duplicate) shows amber "check this file" with the check's sentence; it is imported
// unticked (owner 30 Oct), so it stays out of deduplication until the researcher ticks it.
const FLAGGED = new Set(["off_topic", "empty", "duplicate"]);
function filesTable(d, chk) {
  const checks = {};
  for (const r of (chk && chk.files) || []) checks[`${r.platform}/${r.file}`] = r;
  const incl = (f) => f.imported && f.source_id != null
    ? `<label class="small"><input type="checkbox" class="imp-incl" data-source="${esc(f.source_id)}"${f.included ? " checked" : ""} aria-label="${esc(f.file)} enters deduplication"> ${f.included ? "yes" : "no"}</label>`
    : "";
  // "Random sample of" (openalex-profile-and-sampled-exports D4): empty is a complete export; Save sends the mark
  const sample = (f) => f.imported && f.source_id != null
    ? `<input type="number" min="1" step="1" class="imp-sample" data-sample="${esc(f.source_id)}" value="${f.sample_of == null ? "" : esc(f.sample_of)}" aria-label="${esc(f.file)} is a random sample of how many results (empty: a complete export)"> <button type="button" class="secondary imp-sample-save" data-sample-save="${esc(f.source_id)}">Save</button>`
    : "";
  const row = (f) => {
    const c = checks[`${f.platform}/${f.file}`], flagged = !!(c && FLAGGED.has(c.verdict));
    const status = !f.imported ? '<span class="muted">new</span>' : (flagged ? '<span class="imp-flag">check this file</span>' : '<span class="ok">imported</span>');
    const verdict = c ? `<span class="verdict verdict-${esc(c.verdict === "ok" && c.low ? "low" : c.verdict)}">${esc(verdictLabel(c))}</span>`
      + (flagged ? `<p class="imp-why">${esc(c.message)}${!f.imported ? "." : f.included ? "; it enters deduplication while it is ticked." : "; it stays out of deduplication until you tick it."}</p>` : "") : "";
    return `<tr${flagged ? ' class="imp-flagged"' : ""}><td>${esc(f.platform)}</td><td>${esc(f.file)}</td><td>${status}</td><td>${verdict}</td><td>${incl(f)}</td><td>${sample(f)}</td></tr>`;
  };
  return `<tr><th>platform</th><th>file</th><th>status</th><th>file check</th><th>Enters deduplication</th><th>Random sample of</th></tr>`
    + d.files.map(row).join("")
    + d.summary.map(s => `<tr><td><b>${esc(s.platform)}</b></td><td>${esc(s.files)} file(s)</td><td><b>${esc(s.records)}</b> records</td><td></td><td></td><td></td></tr>`).join("");
}

// Save beside "Random sample of": POST the number (empty clears the mark); a refusal shows the route's message and
// changes nothing. app.js calls it after it fills the table.
function wireSampleFields(root) {
  root.querySelectorAll("[data-sample-save]").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.sampleSave, box = root.querySelector(`[data-sample="${id}"]`);
      const text = String((box && box.value) || "").trim(), of = text === "" ? null : Number(text);
      if ((box && box.validity && box.validity.badInput === true) || (of !== null && !(Number.isInteger(of) && of >= 1))) { $("#imp-msg").textContent = "Give the number of results the database reported (a whole number, 1 or more), or leave the field empty for a complete export. Nothing was changed."; return; }
      btn.disabled = true;
      try {
        const r = await api(`/api/import/sources/${id}/sample`, { method: "POST", body: JSON.stringify({ of }) });
        await loadImport();
        $("#imp-msg").textContent = r.already ? `File ${r.source_id} (${r.file}) was already ${r.sample_of == null ? "a complete export" : "marked so"}; nothing changed.`
          : r.sample_of == null ? `File ${r.source_id} (${r.file}) is a complete export again. Undo it in History.`
          : `File ${r.source_id} (${r.file}) is marked a random sample of ${Number(r.sample_of).toLocaleString("en-US")} results: the Diagnostic report's counts for it are now estimates. Undo it in History.`;
      } catch (e) {   // refused: nothing changed
        btn.disabled = false;
        $("#imp-msg").textContent = e.message;
      }
    };
  });
}

// the platform list of the upload row: the project's platforms, the catalogue's, the folders already under inputs/,
// then "Other…", which shows the free-text key
function platformOptions(plat, d) {
  const seen = new Set(), opts = [];
  const add = (key, label) => { if (!key || seen.has(key)) return; seen.add(key); opts.push([key, label || key]); };
  for (const p of (plat && plat.chosen) || []) add(p.key, p.label);
  for (const p of (plat && plat.catalogue) || []) add(p.key, p.label);
  for (const s of (d && d.summary) || []) add(s.platform);
  return opts;
}
function fillPlatformPick(plat, d) {
  const sel = $("#imp-upload-pick"); if (!sel) return;
  const keep = sel.value;
  sel.innerHTML = `<option value="">choose a platform</option>`
    + platformOptions(plat, d).map(([k, l]) => `<option value="${esc(k)}">${esc(l === k ? k : `${l} (${k})`)}</option>`).join("")
    + `<option value="__other">Other…</option>`;
  if (keep) sel.value = keep;
  showOtherPlatform();
}
function showOtherPlatform() { $("#imp-upload-platform").hidden = $("#imp-upload-pick").value !== "__other"; }
async function loadUploadPlatforms(d) {
  try { fillPlatformPick(await api("/api/platforms"), d); } catch (e) { fillPlatformPick(null, d); }
}
function uploadPlatform() {
  const pick = $("#imp-upload-pick") ? $("#imp-upload-pick").value : "";
  return (pick && pick !== "__other" ? pick : $("#imp-upload-platform").value).trim().toLowerCase();
}

function renderSanity(d) {
  const syn = d.synonyms ? `synonyms v${d.synonyms.version} (${d.synonyms.status})` : "synonyms: none yet (vocabulary check skipped)";
  const head = `<p class="muted small">${d.n_files} file(s) · ${d.benchmark_n} seed paper(s) · ${esc(syn)} · thresholds: ≥${d.thresholds.min_benchmark_hits} seed paper hit(s), ≥${Math.round(d.thresholds.min_vocabulary_overlap * 100)}% vocabulary overlap (a file is off topic only when it misses both)</p>`;
  if (!d.files.length) return head + `<p class="muted">No export files yet.</p>`;
  const rows = d.files.map(r => {
    const bench = r.benchmark_hits == null ? "–" : `${r.benchmark_hits}/${r.benchmark_n}`;
    const vocab = r.vocab_overlap == null ? "–" : `${Math.round(r.vocab_overlap * 100)}%`;
    const fmt = r.format ? (r.profile && !r.profile.startsWith("ris:") ? `${esc(r.format)} <span class="muted small">${esc(r.profile)}</span>` : esc(r.format)) : "?";
    return `<tr><td>${esc(r.platform)}</td><td>${esc(r.file)}${r.imported ? ' <span class="ok small">imported</span>' : ""}</td><td>${fmt}</td><td>${esc(r.n)}</td><td>${esc(bench)}</td><td>${esc(vocab)}</td><td><span class="verdict verdict-${esc(r.verdict === "ok" && r.low ? "low" : r.verdict)}">${esc(verdictLabel(r))}</span></td><td class="small">${esc(r.message)}</td></tr>`;
  }).join("");
  return head + `<table><tr><th>platform</th><th>file</th><th>format</th><th>records</th><th>seed papers</th><th>vocabulary</th><th>verdict</th><th>what it means</th></tr>${rows}</table>`;
}

async function loadImportCheck(given) {
  const box = $("#imp-sanity");
  if (!box) return;
  $("#imp-check-msg").textContent = "checking…";
  try {
    const d = given || await api("/api/import/check");
    box.innerHTML = renderSanity(d);
    $("#imp-check-msg").textContent = "";
  } catch (e) { $("#imp-check-msg").textContent = e.message; }
}

// Re-check redraws the files table too, so its verdict column follows the check
$("#imp-check").onclick = () => loadImport();
if ($("#imp-upload-pick")) $("#imp-upload-pick").onchange = showOtherPlatform;
$("#imp-upload").onclick = async () => {
  const platform = uploadPlatform();
  const f = $("#imp-upload-file").files[0];
  if (!platform || !f) { $("#imp-upload-msg").textContent = "choose a platform and a file"; return; }
  const fd = new FormData();
  fd.append("platform", platform);
  fd.append("file", f);
  const msg = $("#imp-upload-msg");
  try {
    // uploads marked final under a frozen query with validation papers shown: the researcher chooses first (D21, Q2)
    const plan = await api(`/api/import/sources?file=${encodeURIComponent(f.name)}`);
    if (plan.upload_choice) {
      const c = await Refine.askSeen(`Add ${f.name}?`, plan.upload_notice);
      if (!c) { msg.textContent = "Nothing was sent."; return; }
      fd.append("seen_choice", c);
    }
    msg.textContent = "uploading…";
    const d = await api("/api/import/upload", { method: "POST", body: fd });
    msg.innerHTML = `${esc(d.file)}: <span class="verdict verdict-${esc(d.verdict)}">${esc(VERDICT_LABEL[d.verdict] || d.verdict)}</span> ${esc(d.message)}`
      + (d.renamed ? ` <span class="muted">(that name was taken by another file, so this one was saved as ${esc(d.file)})</span>` : "")
      + (d.notice ? `<br><span class="small">${esc(d.notice)}</span>` : "")
      + (d.saved ? `<br><b>Now import it: press "Import the uploaded files" below.</b>` : "");
    if (window.showStageResult) showStageResult(d.stage_result);
    $("#imp-upload-file").value = "";
    await loadImport();
  } catch (e) { msg.textContent = e.message; }
};


// ---- uploads marked final (POST /api/import/final, twin of `wider import final`; undone on the History page) ----
// with files and not final yet, "These uploads are final" is the page's filled main button (D32 F8)
// an uploaded file not imported yet makes "Import the uploaded files" the main button instead, with a line saying so
function showUploadsFinal(at, nFiles, nNew = 0) {
  const btn = $("#imp-final"), msg = $("#imp-final-msg");
  btn.hidden = !!at;
  btn.className = !at && nFiles && !nNew ? "main-act" : "secondary";
  const run = $("#imp-run"); if (run) run.className = nNew ? "main-act" : "secondary";
  const imsg = $("#imp-msg");
  if (imsg && nNew) imsg.textContent = `${nNew === 1 ? "1 uploaded file is" : nNew + " uploaded files are"} not imported yet.`;
  // with no file yet, uploading is the page's main action (D32 F15); once a file is in, it is an outline button
  const up = $("#imp-upload"); if (up) up.className = nFiles || at ? "secondary small-btn" : "main-act";
  msg.textContent = at ? `Uploads marked final on ${fmtWhen(at)}. A file added now reopens the uploads; that is recorded in History. The mark can be undone on the History page.` : "";
}
$("#imp-final").onclick = async () => {
  if (!(await askConfirm("Mark these uploads final? The query validation set opens. A file added later reopens the uploads, which is recorded in History. You can undo the mark on the History page.", "Mark final"))) return;
  const btn = $("#imp-final"); btn.disabled = true; $("#imp-final-msg").textContent = "";
  try {
    const r = await api("/api/import/final", { method: "POST" });
    showUploadsFinal(r.at);
    if (window.afterAction) await afterAction(); else await loadImport();
  } catch (e) { $("#imp-final-msg").textContent = e.message; }
  finally { btn.disabled = false; }
};


// ---- prior-review coverage (local matching) and citation chaining (OpenAlex, opt-in) ----
async function showPriorCoverage(scope) {
  const note = $("#prior-cov-note"), box = $("#prior-cov-tables");
  note.textContent = "checking…";
  try {
    const d = await api(`/api/prior-reviews/coverage?scope=${scope}`);
    if (!d.n_papers) { note.textContent = "No prior reviews added in the Prior reviews task of the Review plan."; box.innerHTML = ""; return; }
    note.textContent = `${d.found}/${d.n_papers} prior-review studies found in at least one ${scope === "corpus" ? "corpus" : "export"}.`;
    box.innerHTML = d.reviews.map(r => {
      const rows = r.rows.map(x => `${esc(x.scope.replace("platform:", ""))} ${x.found}/${x.n}`).join(" · ");
      const unm = r.unmatchable ? ` <span class="muted small">(${r.unmatchable} without title or DOI)</span>` : "";
      const missed = r.missed.length ? `<table><tr><th>#</th><th>missed study</th><th>year</th><th>doi</th></tr>` +
        r.missed.map(m => `<tr><td>${esc(m.position)}</td><td>${esc(m.title || "")}</td><td>${esc(m.year || "")}</td><td class="small">${esc(m.doi || "")}</td></tr>`).join("") + `</table>` :
        `<p class="ok small">all matchable studies found</p>`;
      return `<h2>${esc(r.label)}: ${r.found}/${r.n}${unm}</h2><p class="muted small">${rows}${r.fuzzy ? ` · ${r.fuzzy} fuzzy match(es) to confirm` : ""}</p>${missed}`;
    }).join("");
  } catch (e) { note.textContent = e.message; box.innerHTML = ""; }
}
$("#prior-cov-plat").onclick = () => showPriorCoverage("platforms");
$("#prior-cov-corpus").onclick = () => showPriorCoverage("corpus");
$("#prior-chain-run").onclick = async () => {
  const msg = $("#prior-chain-msg");
  try {
    // uploads marked final under a frozen query with validation papers shown: the researcher chooses first (D21, Q2)
    const plan = await api("/api/prior-reviews");
    let q = "";
    if (plan.chain_choice) {
      const c = await Refine.askSeen("Run citation chaining?", plan.chain_notice);
      if (!c) { msg.textContent = "Nothing was sent."; return; }
      q = `&seen_choice=${c}`;
    }
    msg.textContent = "chaining… (OpenAlex)";
    const d = await api(`/api/prior-reviews/chain?direction=${encodeURIComponent($("#prior-chain-dir").value)}${q}`, { method: "POST" });
    msg.textContent = (d.file ? `${d.n_exported} works written to ${d.file}; import it with "Import the uploaded files".` : `${d.n_new} new works, nothing new to export.`)
      + (d.notice ? ` ${d.notice}` : "");
    if (window.showStageResult && d.stage_result) showStageResult(d.stage_result);
    await loadImport();
  } catch (e) { msg.textContent = e.message; }
};
