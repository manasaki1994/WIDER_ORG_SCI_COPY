// The "Yield" table on the Search strings page (D16): each block's terms with a Count button that asks OpenAlex how
// many works the term finds, one term per click. Same engine as `wider query yield "<term>"` (POST /api/query/yield).
// With the term switch off each term says so; its Count button asks first (the route answers 403 before anything is
// sent and api() opens the consent dialog naming the term switch), so a click sends nothing outside unless the
// researcher allows it there. A proximity group is listed as not counted (OpenAlex search has no proximity operator).
// The count informs and changes nothing.
// Below it, the OpenAlex check (D27): the whole query's count on OpenAlex's title-and-abstract search with its ledger,
// and optionally which Development seeds it retrieves next to the local dry run. Same engine as `wider query yield
// --query [--seeds]` (POST /api/query/yield with query, seeds and the version shown). The seed check needs the DOI
// consent too, so api() may open a second dialog after the term switch's.
(function () {
  const OFF = "term lookups are off (Settings)";
  let last = null;

  // A pure builder: one row per term, from /api/query/render's yield_terms and terms_allowed.
  function tableHtml(blocks, allowed) {
    const rows = (blocks || []).flatMap(b => b.terms.map((t, i) => {
      const cell = !t.countable ? `<span class="muted">not counted (proximity)</span>`
        : `${allowed ? "" : `<span class="muted" data-yield-off>${OFF}</span> `}<button type="button" class="secondary small-btn" data-yield-term="${esc(t.term)}" aria-label="Count ${esc(t.term)} on OpenAlex">Count</button> <span data-yield-out="${esc(t.term)}" aria-live="polite"></span>`;
      return `<tr data-block="${esc(b.key)}"><td>${i === 0 ? esc(b.name) : ""}</td><td>${esc(t.term)}</td><td>${cell}</td></tr>`;
    }));
    if (!rows.length) return `<p class="muted small">No terms yet.</p>`;
    // the sentence of a term count is the same for every term: it is placed once, under the table
    return `<table class="small"><tr><th>block</th><th>term</th><th>Yield</th></tr>${rows.join("")}</table>`
      + `<p class="send-note" data-send="term_yield" hidden></p>`;
  }

  // A pure builder: the check's answer (query_yield's result) as HTML; a seed whose two results differ is marked.
  function checkHtml(res) {
    const lines = (res.ledger_lines || []).map(l => `<li>${esc(l)}</li>`).join("");
    let out = `<p class="small">Sent to OpenAlex (<code>${esc(res.field)}</code>): <code>${esc(res.sent)}</code></p>
      <p data-oa-count><b>${esc(res.line)}</b>${res.count === null && res.status ? ` (HTTP ${esc(res.status)})` : ""}</p>${res.parts ? `
      <p class="small" data-oa-parts>counts of the parts: ${res.parts.map((x, i) => `part ${i + 1}: ${esc(x.count)}`).join("; ")}</p>` : ""}
      <p data-oa-note>${esc(res.note)}</p><ul class="small" aria-label="What was sent to OpenAlex">${lines}</ul>`;
    const s = res.seeds;
    if (s) {
      const on = s.platform ? `dry run on ${esc(s.platform)}` : "no dry run (no profiled platform)";
      out += `<p data-oa-seeds>Development seeds: ${esc(s.n_hit)} of ${esc(s.rows.length)} found on OpenAlex (${on}; ${esc(s.n_sent)} DOIs sent, no Validation seed)</p>
        <table class="small"><tr><th>seed</th><th>title</th><th>dry run</th><th>OpenAlex</th><th></th></tr>${s.rows.map(r =>
          `<tr data-seed="${esc(r.benchmark_id)}"><td>${esc(r.benchmark_id)}</td><td>${esc(r.title)}</td><td>${esc(r.dry_run || "-")}</td><td>${esc(r.openalex)}</td><td${r.note.startsWith("dry run") ? ' class="bad"' : ""}>${esc(r.note)}</td></tr>`).join("")}</table>`;
    }
    return out;
  }

  function wireCheck(d) {
    const box = $("#q-oa-check"), msg = $("#q-oa-msg");
    if (!box) return;
    const version = d.row ? d.row.version : null;
    box.querySelectorAll("[data-oa-run]").forEach(b => b.onclick = async () => {
      const seeds = b.dataset.oaRun === "seeds";
      b.disabled = true;
      msg.textContent = "asking OpenAlex…";
      try {
        const res = await api("/api/query/yield", { method: "POST", body: JSON.stringify({ query: true, seeds, version }) });
        if (!last.terms_allowed) render({ ...last, terms_allowed: true });   // allowed in the dialog
        $("#q-oa-out").innerHTML = checkHtml(res);
        $("#q-oa-msg").textContent = "";
      } catch (e) {
        $("#q-oa-msg").textContent = e.message;
      } finally {
        b.disabled = false;
      }
    });
  }

  function outFor(box, term) {
    return [...box.querySelectorAll("[data-yield-out]")].find(o => o.dataset.yieldOut === term);
  }

  function render(d) {
    const box = $("#q-yield");
    if (!box) return;
    last = d;
    box.innerHTML = tableHtml(d.yield_terms, !!d.terms_allowed);
    wireCheck(d);
    box.querySelectorAll("[data-yield-term]").forEach(b => b.onclick = async () => {
      const term = b.dataset.yieldTerm;
      b.disabled = true;
      outFor(box, term).textContent = "counting…";
      try {
        const res = await api("/api/query/yield", { method: "POST", body: JSON.stringify({ term }) });
        if (!last.terms_allowed) render({ ...last, terms_allowed: true });   // allowed in the dialog: no "off" left
        outFor($("#q-yield"), term).textContent = res.line;
      } catch (e) {
        outFor(box, term).textContent = e.message;
      } finally {
        b.disabled = false;
      }
    });
  }

  window.TermYield = { render, tableHtml, checkHtml, OFF };
})();
