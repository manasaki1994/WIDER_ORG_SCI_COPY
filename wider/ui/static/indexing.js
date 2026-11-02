// The "Indexing check" card on the Search results page (D10, task 2.10): per platform, the seed papers it did not
// retrieve, the known-item string to paste into that database by hand, and the researcher's verdict (indexed, not
// indexed, unknown). Same engine as `wider query indexing list|set` (GET/POST /api/query/indexing). Nothing is
// searched from here. A hidden Validation paper never reaches this card: the engine leaves it out (D9, F2).
(function () {
  const VERDICTS = [["indexed", "Indexed"], ["not_indexed", "Not indexed"], ["unknown", "Unknown"]];
  const GROUP_WORDS = { not_indexed: "not in the database", indexed_missed: "indexed but not retrieved",
                        unknown: "indexing unknown", unchecked: "not checked yet" };

  function groupsHtml(key, groups) {
    const parts = Object.entries(GROUP_WORDS).filter(([g]) => (groups[g] || []).length)
      .map(([g, w]) => `<span data-group="${esc(g)}">${esc(w)}: ${esc(groups[g].join(", "))}</span>`);
    return parts.length ? `<p class="small" data-groups="${esc(key)}">${parts.join(" · ")}</p>` : "";
  }

  function seedRow(key, s) {
    const str = s.string
      ? `<code data-string>${esc(s.string)}</code> <button class="secondary small-btn" data-idx-copy="${esc(s.string)}" aria-label="Copy the known-item string for ${esc(s.benchmark_id)}">Copy</button>`
      : `<span class="muted">search the title by hand</span>`;
    const alt = s.alternative ? `<br><span class="muted small">or <code>${esc(s.alternative)}</code></span>` : "";
    const btns = VERDICTS.map(([v, w]) => `<button class="secondary small-btn" data-idx-verdict="${v}" data-seed="${esc(s.benchmark_id)}"
      data-platform="${esc(key)}" aria-pressed="${s.verdict === v ? "true" : "false"}">${w}</button>`).join(" ");
    return `<tr data-seed="${esc(s.benchmark_id)}"><td>${esc(s.benchmark_id)}</td><td>${esc(s.set)}</td>
      <td class="small">${esc(s.title || "")}</td><td class="small">${str}${alt}</td><td>${btns}</td>
      <td class="small" data-diagnosis>${esc(s.diagnosis)}</td></tr>`;
  }

  // A pure builder: the card's body from GET /api/query/indexing.
  function html(d) {
    const plats = Object.entries(d.platforms || {}).map(([k, pl]) => {
      const head = `<h3>${esc(pl.label || k)} <span class="muted small">${esc(pl.files)} file(s), ${esc(pl.retrieved)} seed(s) retrieved, ${esc(pl.missed)} missed</span></h3>`;
      if (pl.note) return `${head}<p class="muted small" data-note="${esc(k)}">${esc(pl.note)}</p>`;
      if (!(pl.seeds || []).length) return `${head}<p class="muted small">Every seed paper shown was retrieved.</p>`;
      const tpl = pl.template_note ? `<p class="muted small">Template ${pl.verified ? "checked" : "not checked by hand yet"}: ${esc(pl.template_note)}</p>` : "";
      return `${head}${tpl}<table class="idx-table" data-platform="${esc(k)}"><tr><th>seed</th><th>set</th><th>title</th>
        <th>known-item string</th><th>verdict</th><th>diagnosis</th></tr>${pl.seeds.map(s => seedRow(k, s)).join("")}</table>
        ${groupsHtml(k, pl.groups || {})}`;
    }).join("");
    return `<p class="muted small" id="idx-note">${esc(d.note || "")}</p>${plats}`;
  }

  async function setVerdict(b) {
    const msg = $("#idx-msg");
    b.disabled = true;
    try {
      const r = await api("/api/query/indexing", { method: "POST", body: JSON.stringify({
        benchmark_id: b.dataset.seed, platform: b.dataset.platform, verdict: b.dataset.idxVerdict }) });
      await load();
      $("#idx-msg").textContent = r.already ? `${r.benchmark_id} on ${r.platform} is already ${r.verdict}.`
        : `${r.benchmark_id} on ${r.platform}: ${r.verdict} (${r.diagnosis}). Undo it in History.`;
    } catch (e) { msg.textContent = e.message; b.disabled = false; }
  }

  async function load() {
    const out = $("#idx-out"); if (!out) return;
    try {
      out.innerHTML = html(await api("/api/query/indexing"));
      out.querySelectorAll("[data-idx-verdict]").forEach(b => { b.onclick = () => setVerdict(b); });
      out.querySelectorAll("[data-idx-copy]").forEach(b => { b.onclick = async () => {
        try { await navigator.clipboard.writeText(b.dataset.idxCopy); b.textContent = "Copied"; } catch (e) { b.textContent = "Select and copy"; }
      }; });
    } catch (e) { out.innerHTML = ""; $("#idx-msg").textContent = e.message; }
  }

  globalThis.IndexingCheck = { load, html, setVerdict };
})();
