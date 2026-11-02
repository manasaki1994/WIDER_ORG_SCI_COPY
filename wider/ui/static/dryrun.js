// The "Dry run" card on the Search strings page (D6): the query evaluated on the seed papers under each platform's
// profile, on this computer only. Same engine as `wider query dry-run` (POST /api/query/dry-run). A state the profile
// cannot decide is "uncertain"; its reason shows in the table and, on focus or hover of the state, below the table.
// Under each platform: the terms (expand a term for the words each * matched, `--terms`) and the grid with the
// minimal cover (`--grid`); below all platforms the heatmap. The text lines come from the engine, so page and CLI agree.
(function () {
  const SETUP_HINT = "no seed papers";

  // A pure builder: one platform's table (seed, set, state, terms matched, why) and its recall line.
  function platformHtml(key, label, res) {
    const p = res.profile || {};
    const stem = p.min_stem == null ? "none" : p.min_stem;
    const rows = (res.seeds || []).map(s => {
      const why = s.why || "";
      const cls = s.state === "hit" ? "ok" : (s.state === "miss" ? "bad" : "unc");
      return `<tr><td>${esc(s.benchmark_id)}</td><td>${esc(s.set)}</td>
        <td><span tabindex="0" class="dr-state ${cls}" data-reason="${esc(why)}" title="${esc(why)}">${esc(s.state)}</span></td>
        <td class="small">${esc((s.terms || []).join("; "))}</td><td class="small">${esc(why)}</td></tr>`;
    }).join("");
    return `<h3>${esc(label || key)} <span class="muted small">(profile: minimum stem ${esc(stem)})</span></h3>
      <table class="dr-table" data-platform="${esc(key)}"><tr><th>seed</th><th>set</th><th>state</th><th>terms matched</th><th>why</th></tr>${rows}</table>
      <p class="small" data-recall="${esc(key)}">${esc(label || key)}: ${esc(res.recall.text)}</p>
      ${termsHtml(key, res.terms_text || [])}${gridHtml(key, res)}`;
  }

  // One expandable row per term: the summary is the term's line, the body its stem lines (indented in the CLI).
  function termsHtml(key, lines) {
    const groups = [];
    lines.forEach(l => { if (l.startsWith("  ")) { if (groups.length) groups[groups.length - 1].body.push(l.trim()); }
                         else groups.push({ head: l, body: [] }); });
    const rows = groups.map(g => g.body.length
      ? `<details class="dr-term"><summary class="small">${esc(g.head)}</summary><ul class="small">${
          g.body.map(b => `<li>${esc(b)}</li>`).join("")}</ul></details>`
      : `<p class="small dr-term">${esc(g.head)}</p>`).join("");
    return `<details class="dr-terms" data-terms="${esc(key)}"><summary>Terms and what each * matched</summary>${rows}</details>`;
  }

  // The grid as a table (x hit, ? uncertain, empty no match; each cell named for a screen reader), then the cover.
  function gridHtml(key, res) {
    const g = res.grid; if (!g) return "";
    const word = { hit: "hit", uncertain: "uncertain", "": "no match" };
    const head = `<tr><th>term</th>${g.seeds.map(s => `<th>${esc(s)}</th>`).join("")}</tr>`;
    const rows = g.rows.map(r => `<tr><td class="small">${esc(r.block)}: ${esc(r.term)}</td>${r.cells.map((c, j) =>
      `<td class="dr-cell ${c === "hit" ? "ok" : (c ? "unc" : "")}" aria-label="${esc(r.term)} on ${esc(g.seeds[j])}: ${word[c]}">${
        c === "hit" ? "x" : (c ? "?" : "")}</td>`).join("")}</tr>`).join("");
    const cover = (res.cover || []).map(c => `${c.block}: ${c.term}`).join("; ") || "none (no seed is retrieved)";
    const none = (res.no_seed || []).map(c => `${c.block}: ${c.term}`).join("; ");
    return `<details class="dr-grid" data-grid="${esc(key)}"><summary>Grid and minimal cover</summary>
      <table class="dr-table">${head}${rows}</table>
      <p class="small" data-cover="${esc(key)}">minimal cover: ${esc(cover)}</p>
      ${none ? `<p class="small" data-no-seed="${esc(key)}">matches no seed paper: ${esc(none)}</p>` : ""}</details>`;
  }

  function resultHtml(d, labels) {
    const notes = (d.notices || []).map(n => `<p class="warn-text small">${esc(n)}</p>`).join("");
    const plats = Object.entries(d.platforms || {}).map(([k, r]) => platformHtml(k, labels[k], r)).join("");
    // the heatmap is built by the engine from escaped text (plots.heatmap); its <desc> lists every cell
    const heat = d.grid_svg ? `<details class="dr-heatmap"><summary>Heatmap of terms against seed papers</summary>${d.grid_svg}</details>` : "";
    const files = (d.grid_files || []).map(f => `<code>${esc(f)}</code>`).join(", ");
    return `<p class="small">synonyms v${esc(d.version)} (${esc(d.status)}) · ${esc((d.seeds || []).length)} seed papers</p>${notes}${plats}${globalThis.CoverageBars ? globalThis.CoverageBars.html(d, labels) : ""}${heat}
      <p class="muted small">Written to <code>${esc(d.run_dir || "")}/dry_run.json</code>${files ? `, ${files}` : ""}.</p>`;
  }

  // The error text as it comes from the engine; "no seed papers" gets a link to the Seed papers task (D17).
  function errorHtml(msg) {
    const link = String(msg).startsWith(SETUP_HINT) ? ' <a href="#setup?task=seeds" data-dr-setup>Open Seed papers</a>' : "";
    return `<span class="bad">${esc(msg)}</span>${link}`;
  }

  let labels = {};
  async function load() {
    const sel = $("#dr-platform"); if (!sel) return;
    try {
      const d = await api("/api/query/profiles");
      labels = Object.fromEntries(d.platforms.map(p => [p.key, p.label]));
      const keep = sel.value;
      sel.innerHTML = `<option value="">Every platform with a profile</option>` + d.platforms.map(p =>
        `<option value="${esc(p.key)}"${p.profile ? "" : " disabled"}>${esc(p.label)}${p.profile ? "" : " (no profile)"}</option>`).join("");
      if ([...sel.options].some(o => o.value === keep && !o.disabled)) sel.value = keep;
    } catch (e) { $("#dr-msg").textContent = e.message; }
  }

  async function run() {
    const btn = $("#dr-run"), out = $("#dr-out"), msg = $("#dr-msg");
    const v = $("#dr-version").value.trim(), plat = $("#dr-platform").value;
    btn.disabled = true; msg.textContent = "running…"; $("#dr-reason").textContent = "";
    try {
      const d = await api("/api/query/dry-run", { method: "POST",
        body: JSON.stringify({ version: v ? Number(v) : null, platform: plat || null }) });
      out.innerHTML = resultHtml(d, labels);
      msg.textContent = (d.interview && d.interview.line) || "";   // the refinement questions this run raised (2b)
      if (globalThis.Questions) Questions.refresh();
      if (globalThis.Rounds) Rounds.load();   // the run is kept for its round: the Rounds card shows it at once
      out.querySelectorAll(".dr-state").forEach(s => {
        const show = () => { $("#dr-reason").textContent = s.dataset.reason ? `${s.textContent}: ${s.dataset.reason}` : s.textContent; };
        s.onfocus = show; s.onmouseenter = show;
      });
    } catch (e) {
      out.innerHTML = ""; msg.innerHTML = errorHtml(e.message);
      const a = msg.querySelector("[data-dr-setup]");
      if (a) a.onclick = (ev) => { ev.preventDefault(); location.hash = "setup?task=seeds"; };
    } finally { btn.disabled = false; }
  }

  const b = $("#dr-run"); if (b) b.onclick = run;
  window.DryRun = { load, run, platformHtml, resultHtml, errorHtml, termsHtml, gridHtml };
})();
