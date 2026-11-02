// The per-database coverage plot of a dry run (D7, task 2.7): the SVG the engine drew (the same one it wrote to
// runs/query/<stamp>/coverage_by_platform.svg) and a text table with the same hits, uncertain and misses per platform.
// The SVG comes from wider/search/coverage_plot.py, which escapes every label; the table is built here from
// d.coverage, the rows the SVG was drawn from, so the two cannot disagree.
(function () {
  function tableHtml(rows, labels) {
    const body = (rows || []).map(r => `<tr data-platform="${esc(r.platform)}"><td>${esc((labels && labels[r.platform]) || r.label || r.platform)}</td>
      <td data-state="hits">${esc(r.hits)}</td><td data-state="uncertain">${esc(r.uncertain)}</td>
      <td data-state="misses">${esc(r.misses)}</td><td>${esc(r.total)}</td></tr>`).join("");
    return `<table class="cov-bars-table"><caption class="small">Coverage per database (same numbers as the plot)</caption>
      <tr><th>platform</th><th>hits</th><th>uncertain</th><th>misses</th><th>seeds</th></tr>${body}</table>`;
  }

  // The plot and the table, or "" when the dry run has no coverage rows.
  function html(d, labels) {
    if (!d || !(d.coverage || []).length) return "";
    const file = d.run_dir ? `<p class="muted small">Plot written to <code>${esc(d.run_dir)}/coverage_by_platform.svg</code>.</p>` : "";
    return `<div class="cov-bars"><h3>Coverage per database</h3><div class="cov-bars-plot">${d.coverage_svg || ""}</div>
      ${tableHtml(d.coverage, labels)}${file}</div>`;
  }

  globalThis.CoverageBars = { html, tableHtml };
})();
