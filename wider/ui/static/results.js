// Results dashboard (change results-dashboard-and-ris, design D1-D9): one read-only view of a protocol's result set,
// from GET /api/results, the route `wider results --json` prints. The three selects are twins of the CLI's PROTOCOL,
// --set and --sample; choosing a set only shows it (it never calls the route that makes a set active), and opening
// the page computes no estimate and records no look. The builders return HTML strings only (every value through
// `esc`, common.js); the controller is given its parts and api, so tests run it under Node with fakes. Sentences and
// warnings are normal text, black or red, never grey.
const Results = (() => {
  const opt = (v, label, on) => `<option value="${esc(v)}"${on ? " selected" : ""}>${esc(label)}</option>`;
  const fileLink = (path, text) => `<a href="/api/outputs/file?path=${encodeURIComponent(path)}" download>${esc(text || path)}</a>`;
  const NOTHING = "no screening decisions yet";

  // the agreement as its text lines (agreement.markdown, as the CLI prints them): table rows as a table, "- " lines
  // as a list, other lines as sentences
  function linesHtml(lines) {
    const out = []; let table = [], list = [];
    const flush = () => {
      if (table.length) {
        const rows = table.filter(r => !/^\|[-:|]+\|$/.test(r)).map(r => r.slice(1, -1).split("|").map(c => c.trim().replace(/\\\\/g, "\\")));
        out.push(`<table class="rec-table">${rows.map((r, i) => `<tr>${r.map(c => i ? `<td>${esc(c)}</td>` : `<th>${esc(c)}</th>`).join("")}</tr>`).join("")}</table>`);
        table = [];
      }
      if (list.length) { out.push(`<ul>${list.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`); list = []; }
    };
    for (const l of lines || []) {
      if (l.startsWith("|")) { if (list.length) flush(); table.push(l); continue; }
      if (l.startsWith("- ")) { if (table.length) flush(); list.push(l.slice(2)); continue; }
      flush();
      if (l.trim()) out.push(`<p>${esc(l)}</p>`);
    }
    flush();
    return out.join("");
  }

  function headingHtml(hd) {
    return `<h2 data-results-title>${esc(hd.title)}</h2><p data-results-models>${esc(hd.models)}</p>`
      + (hd.role_note ? `<p>${esc(hd.role_note)}</p>` : "") + (hd.shared || []).map(x => `<p>${esc(x)}</p>`).join("");
  }

  // the stored recall lines "as of" their time, the outcome lines in the warning style only when the target is shown
  // not met (an inconclusive result is not a warning); or the one sentence that says what is missing (black)
  function recallHtml(r) {
    if (r.state !== "current") return `<p data-recall-sentence>${esc(r.sentence)}</p>`;
    const warn = new Set(r.warning_lines || []);
    return (r.sentence ? `<p data-recall-sentence>${esc(r.sentence)}</p>` : "")
      + (r.headline ? `<p data-recall-headline><strong>${esc(r.headline)}</strong></p>` : "")
      + (r.as_of ? `<p>as of ${esc(fmtWhen(r.as_of))}</p>` : "")
      + (r.lines || []).map(l => warn.has(l) ? `<p class="warn-line">${esc(l)}</p>` : `<p>${esc(l)}</p>`).join("");
  }

  // the relevant-found chart (design D10, D11, D14): three lines through the band ends (relevant blue, kept green
  // dashed, missed violet dotted), a boundary line between bands, range bars beside the points (not centred on them,
  // no caps), titled axes. The svg is a group named by its <title> and <desc>; each band end has a focusable mark whose
  // accessible name is its tooltip text, everything else is aria-hidden. No target or stopping line.
  const SERIES = [["rel", "relevant", "var(--blue)", ""], ["kept", "kept", "var(--green)", "6 3"], ["missed", "missed", "var(--violet)", "2 3"]];
  function chartSvg(c) {
    const ends = c.ends || [];
    if (!ends.length) return "";
    const W = 640, H = 300, L = 64, R = 32, T = 16, B = 56;
    const all = (c.rows || []).map(r => r.end).filter(e => e != null);
    const xmax = Math.max(...all, ...ends.map(e => e.end)) || 1;
    const ymax = Math.max(1, ...ends.flatMap(e => SERIES.map(([k]) => Math.max(e[k] || 0, e[k + "_high"] || 0))));
    const x = (v) => (L + (W - L - R) * v / xmax).toFixed(1), y = (v) => (H - B - (H - T - B) * v / ymax).toFixed(1);
    const t = (px, py, s, more) => `<text x="${px}" y="${py}" font-size="13" fill="currentColor"${more || ""}>${esc(s)}</text>`;
    const bounds = all.slice(0, -1).map(e => `<line data-band-boundary="${esc(e)}" x1="${x(e)}" x2="${x(e)}" y1="${T}" y2="${H - B}" stroke="var(--line)"/>`
      + t(x(e), H - B + 16, e, ' text-anchor="middle"')).join("") + t(x(xmax), H - B + 16, xmax, ' text-anchor="middle"');
    const line = ([k, name, col, dash]) => `<path data-series="${name}" d="M${x(0)},${y(0)}${ends.map(e => ` L${x(e.end)},${y(e[k])}`).join("")}" fill="none" stroke="${col}" stroke-width="2"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`
      + ends.map(e => `<circle cx="${x(e.end)}" cy="${y(e[k])}" r="3" fill="${col}"/>`).join("");
    const bars = c.has_bounds ? SERIES.map(([k, name, col], i) => ends.filter(e => e[k + "_low"] != null).map(e =>
      `<rect data-range="${name}" x="${(Number(x(e.end)) + 4 + 7 * i).toFixed(1)}" y="${y(e[k + "_high"])}" width="6" height="${Math.max(1, y(e[k + "_low"]) - y(e[k + "_high"])).toFixed(1)}" fill="${col}" fill-opacity="0.25"/>`).join("")).join("") : "";
    const rows = c.rows || [];
    const marks = c.old_series ? "" : ends.map((e, i) => `<g data-band-mark="${esc(e.end)}" role="img" tabindex="0" aria-label="${esc((rows[i] || {}).tooltip || "")}">`
      + `<rect x="${(Number(x(e.end)) - 14).toFixed(1)}" y="${T}" width="40" height="${H - B - T}" fill="transparent"/></g>`).join("");
    return `<svg class="results-chart" role="group" aria-labelledby="curve-title curve-desc" viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">`
      + `<title id="curve-title">${esc(c.title)}</title><desc id="curve-desc">${esc(c.desc || "")}</desc>`
      + `<g aria-hidden="true">`
      + `<line x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}" stroke="currentColor"/><line x1="${L}" x2="${L}" y1="${T}" y2="${H - B}" stroke="currentColor"/>`
      + t(L - 6, Number(y(ymax)) + 4, Number(ymax.toFixed(1)).toLocaleString("en"), ' text-anchor="end"') + t(L - 6, Number(y(0)) + 4, 0, ' text-anchor="end"')
      + bounds + bars + SERIES.map(line).join("")
      + t((L + W - R) / 2, H - 12, (c.axes || {}).x, ' text-anchor="middle" data-axis="x"')
      + t(16, (T + H - B) / 2, (c.axes || {}).y, ` text-anchor="middle" data-axis="y" transform="rotate(-90 16 ${(T + H - B) / 2})"`)
      + `</g>${marks}</svg>`;
  }
  // the legend as text with a sample of each line (and of the range bar where there are bounds)
  function legendHtml(c) {
    const sw = (col, dash, bar) => `<svg aria-hidden="true" width="24" height="10">${bar ? `<rect x="9" y="0" width="6" height="10" fill="${col}" fill-opacity="0.25"/>`
      : `<line x1="0" x2="24" y1="5" y2="5" stroke="${col}" stroke-width="2"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`}</svg>`;
    return `<p data-curve-legend>${(c.legend || []).map((l, i) => `<span class="inline">${sw(SERIES[i][2], SERIES[i][3])} ${esc(l)}</span>`).join(" ")}`
      + (c.range_legend ? ` <span class="inline">${sw("currentColor", "", true)} ${esc(c.range_legend)}</span>` : "") + `</p>`;
  }
  // the table of `wider results`: its head and cells from the route; cells past the head (the outside-range sentence)
  // as a row of their own
  function curveTableHtml(c) {
    const head = c.head || [], n = head.length;
    const groups = c.old_series ? "" : `<tr><th colspan="5">observed</th><th colspan="${n - 5}">estimated</th></tr>`;
    const row = (cells) => {
      const main = cells.slice(0, n), rest = cells.slice(n);
      const tds = main.map((x, i) => `<td${i === main.length - 1 && main.length < n ? ` colspan="${n - i}"` : ""}>${esc(x)}</td>`).join("");
      return `<tr>${tds}</tr>` + rest.map(x => `<tr><td colspan="${n}">${esc(x)}</td></tr>`).join("");
    };
    return `<table class="rec-table" data-curve-table>${groups}<tr>${head.map(h => `<th>${esc(h)}</th>`).join("")}</tr>${(c.cells || []).map(row).join("")}</table>`;
  }
  function curveHtml(c) {
    if (c.state === "stale") {
      return `<p data-curve-sentence>${esc(c.sentence)}</p>`
        + `<p><button class="secondary" data-curve-again>${esc(c.again_label)}</button></p><p data-curve-msg role="status"></p>${c.again_command ? cmdFold(`<code>${esc(c.again_command)}</code>`) : ""}`;
    }
    if (c.state !== "current") {
      return `<p data-curve-sentence>${esc(c.sentence || "No chart: no recall estimate is shown.")}</p>`;
    }
    const stop = c.stop ? `<p data-curve-stop>${esc(c.stop.sentence)}</p><p><a href="${esc(c.stop.href)}" data-curve-label-link>${esc(c.stop.link)}</a></p>` : "";
    return `<p data-curve-label>${esc(c.label)}</p>`
      + (c.ends && c.ends.length ? `<div class="curve-wrap">${chartSvg(c)}<div class="curve-tip" data-curve-tip aria-hidden="true" hidden></div></div>${legendHtml(c)}` : "")
      + (c.notes || []).map(x => `<p>${esc(x)}</p>`).join("") + stop
      + (c.outside ? `<p data-curve-outside>${esc(c.outside_text)}</p>` : "")
      + curveTableHtml(c)
      + (c.method ? `<details data-curve-method><summary>${esc(c.method.summary)}</summary><p>${esc(c.method.text)}</p></details>` : "");
  }
  // one tooltip for the band marks under `root`: shown on hover and focus, hidden by Escape, blur or leaving the mark
  function bindTip(root) {
    const tip = () => root.querySelector("[data-curve-tip]");
    const mark = (e) => (e.target && e.target.closest ? e.target.closest("[data-band-mark]") : null);
    const hide = () => { const t = tip(); if (t) t.hidden = true; };
    const show = (e) => {
      const m = mark(e), t = tip();
      if (!m || !t) return;
      t.textContent = m.getAttribute("aria-label"); t.hidden = false;
      if (m.getBoundingClientRect && t.parentElement) {
        const a = m.getBoundingClientRect(), b = t.parentElement.getBoundingClientRect();
        t.style.left = `${Math.max(0, Math.min(a.left - b.left, b.width - 320))}px`; t.style.top = `${a.top - b.top + 8}px`;
      }
    };
    root.addEventListener("mouseover", show); root.addEventListener("focusin", show);
    root.addEventListener("mouseout", (e) => { if (mark(e)) hide(); }); root.addEventListener("focusout", hide);
    root.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  }

  function countsHtml(k, lines) {
    // the provisional warning comes first, at normal size (D32 F4); "not yet checked" shows only when above zero
    return (k.provisional_line ? `<p class="warn-line">${esc(k.provisional_line)}</p>` : "")
      + `<table class="rec-table" aria-label="Record counts">${(lines || []).filter(([key]) => key !== "not_checked" || k[key]).map(([key, label]) => `<tr><th scope="row" class="kv">${esc(label)}</th><td>${esc(Number(k[key] || 0).toLocaleString("en"))}</td></tr>`).join("")}</table>`;
  }
  // the Export records card (change decision-set-exports-and-found-chart, design D2-D8): the words are those of
  // wider/recall/selection.py; every choice is a twin of a `wider export selection` option
  const PRESETS = [["include", "Include"], ["include+maybe", "Include + Maybe"], ["maybe", "Maybe only"], ["exclude", "Exclude"],
    ["unresolved", "Unresolved only"], ["every", "Every decision"]];
  const PATHWAYS = [["agreed", "both configurations agreed"], ["third_read", "resolved by the third model"],
    ["split", "unresolved disagreement"], ["researcher", "decided by a researcher"]];
  const PATTERNS = [["include_exclude", "include / exclude"], ["include_maybe", "include / maybe"],
    ["maybe_exclude", "maybe / exclude"], ["agreed", "both reads the same"]];
  const FORMATS = [["ris", "RIS"], ["bib", "BibTeX"], ["csv", "CSV"]];
  const PACKAGE_ADD = [["include", "Include only"], ["maybe", "Maybe only"], ["exclude", "Exclude only"],
    ["unresolved", "Unresolved only"], ["selection", "the current selection"]];
  const ABSTRACTS = "Abstracts are the publishers' text; leave them out if you pass these files to someone else.";
  // the older `wider export references --which` sets as the card's choices (design D6's parity)
  const OLDER = { include: ["include", []], "include+maybe": ["include+maybe", []], maybe: ["maybe", []], exclude: ["exclude", []],
    agreed: ["every", ["agreed"]], third_read: ["every", ["third_read"]], split: ["every", ["split"]], researcher: ["every", ["researcher"]] };
  const blankChoice = () => ({ preset: "include+maybe", pathways: [], patterns: [], final_only: false, unticked: [],
    formats: ["ris"], abstracts: true, add: [], pkg_formats: ["ris"] });
  const box = (field, value, label, on) => `<label class="inline"><input type="checkbox" data-export-field="${esc(field)}" value="${esc(value)}"${on ? " checked" : ""}> ${esc(label)}</label>`;
  const boxes = (field, list, chosen) => list.map(([v, l]) => box(field, v, l, chosen.includes(v))).join(" ");
  function exportCardHtml(st, command, selection) {
    st = st || blankChoice();
    return `<div data-export-card><h4 id="export-records">Export records</h4>`
      + `<p><label class="inline">Records <select data-export-field="preset">${PRESETS.map(([v, l]) => opt(v, l, v === st.preset)).join("")}</select></label></p>`
      + `<details data-export-filters><summary>Filter by how the decision was reached</summary>`
      + `<fieldset><legend>How the decision was reached (any ticked; none ticked: every route)</legend>${boxes("pathway", PATHWAYS, st.pathways)}</fieldset>`
      + `<fieldset><legend>First two reads (any ticked)</legend>${boxes("pattern", PATTERNS, st.patterns)}</fieldset>`
      + `<p>${box("final_only", "true", "Leave out unresolved records", st.final_only)}</p></details>`
      + `<details data-export-choose><summary>Choose records one by one</summary><div data-export-records></div></details>`
      + `<fieldset><legend>Formats</legend>${boxes("format", FORMATS, st.formats)}</fieldset>`
      + `<p>${box("abstracts", "true", "Include abstracts", st.abstracts)} ${esc(ABSTRACTS)}</p>`
      + `<p data-export-count role="status"></p><p data-export-breakdown></p>`
      + `<p><button class="main-act" data-export-go>Export</button> <button class="secondary" data-export-audit>Audit CSV</button></p>`
      + `<p data-export-msg role="status"></p>`
      + `<p><button class="secondary" data-export-package>Download export package</button></p>`
      + `<details data-export-customize><summary>Customize package</summary>`
      + `<fieldset><legend>Also put in the package</legend>${boxes("add", PACKAGE_ADD, st.add)}</fieldset>`
      + `<fieldset><legend>Package formats</legend>${boxes("pkg_format", FORMATS, st.pkg_formats)}</fieldset></details>`
      + `<div data-export-package-list></div>`
      + `<p><button class="secondary" data-export-all>Every set at once</button></p>`
      + `<div data-export-index></div>`
      + cmdFold(`<code>${esc(selection || "wider export selection")}</code>` + (command ? `, <code>${esc(command)}</code>` : "")) + `</div>`;
  }
  // the Import adjudicator's decisions card (change partial-adjudication-import, design D13): a chosen file is counted
  // first (a dry run), "Import" stays off until that count worked; the twin of `wider screen import-adjudication`
  function adjudicationCardHtml(protocol, resultSet) {
    return `<div data-adj-card><h4 id="import-adjudication">Import adjudicator's decisions</h4>`
      + `<p data-adj-into>Into set ${esc(resultSet)} of ${esc(protocol)}, the set shown. Records without a decision in the file keep their decision; the set shown stays unchanged and a new set is made active.</p>`
      + `<p>Export the records above as CSV, give them to the adjudicator, and have them write include, exclude or maybe in a column (named adjudication, or the name you type under Column) for the records they decided. An adjudicator column, if present, names who decided each row.</p>`
      + `<p><label class="inline">File (CSV) <input type="file" accept=".csv,text/csv" data-adj-file></label></p>`
      + `<p><label class="inline">Adjudicator <input type="text" data-adj-field="adjudicator" autocomplete="off"></label> `
      + `<label class="inline">Column <input type="text" data-adj-field="column" value="adjudication"></label></p>`
      + `<div data-adj-msg role="status"></div>`
      + `<p><button class="secondary" data-adj-go disabled>Import</button></p>`
      + cmdFold(`<code>${esc(`wider screen import-adjudication ${protocol} FILE --adjudicator NAME --set ${resultSet} --dry-run`)}</code>`) + `</div>`;
  }
  // the result's lines as sentences in normal text; a conflicting second row in red
  function adjudicationLinesHtml(r) {
    const red = new Set((r.conflicts || []).map(x => x.text));
    return (r.lines || []).map(l => `<p${red.has(l) ? ' class="bad"' : ""}>${esc(l)}</p>`).join("");
  }
  const pathWord = (p) => (PATHWAYS.find(([v]) => v === p) || [p, p])[1];
  // the filtered selection's records, each ticked unless unticked (title, year, decision, status, pathway)
  function recordsHtml(rows, unticked) {
    if (!rows.length) return `<p>No record in this selection.</p>`;
    return `<table class="rec-table"><tr><th>record</th><th>year</th><th>decision</th><th>status</th><th>pathway</th></tr>`
      + rows.map(r => `<tr data-export-record="${esc(r.record_id)}"><td><label><input type="checkbox" data-export-field="record" value="${esc(r.record_id)}"${unticked.includes(r.record_id) ? "" : " checked"}> ${esc(r.title)}</label></td>`
        + `<td>${esc(r.year)}</td><td>${esc(r.decision)}</td><td>${esc(r.status)}</td><td>${esc(pathWord(r.pathway))}</td></tr>`).join("")
      + `</table>`;
  }
  function packageListHtml(r) {
    return `<p>The package ${esc(r.file.split("/").pop())} will hold:</p><ul data-export-package-files>`
      + r.files.map(f => `<li>${esc(f.name)}: ${esc(f.label)}</li>`).join("") + `</ul>`;
  }
  // every decision set at once: the index as a table, each set's files linked; an empty set has none
  function indexHtml(r) {
    return `<p>${fileLink(r.index)}</p><table class="rec-table"><tr><th>set</th><th>records</th><th>files</th></tr>`
      + r.sets.map(s => `<tr data-refs-set="${esc(s.set)}"><td>${esc(s.set)}</td><td>${esc(Number(s.records).toLocaleString("en"))}</td>`
        + `<td>${s.files.length ? s.files.map(f => fileLink(f)).join(", ") : "no files"}</td></tr>`).join("") + `</table>`;
  }
  function refsHtml(rs, st, d) {
    return `<table class="rec-table" aria-label="Decision sets"><thead class="sr-only"><tr><th>set</th><th>records</th></tr></thead>${rs.sets.map(s => `<tr data-ref-set="${esc(s.set)}"><th scope="row" class="kv">${esc(s.label)}</th><td>${esc(Number(s.records).toLocaleString("en"))}</td></tr>`).join("")}</table>`
      + exportCardHtml(st, rs.all_command, rs.command)
      + (d && d.protocol ? adjudicationCardHtml(d.protocol, d.result_set) : "");
  }
  function docsHtml(docs) {
    return `<table class="rec-table" aria-label="Report files"><thead class="sr-only"><tr><th>document</th><th>file</th><th>written</th></tr></thead>${docs.map(d => `<tr data-doc="${esc(d.path)}"><th scope="row" class="kv">${esc(d.label)}</th>`
      + (d.waits ? `<td>${esc(d.path)}</td><td data-doc-waits>${esc(d.waits_text)}</td>`
        : d.exists ? `<td>${d.link ? fileLink(d.path) : esc(d.path)}</td><td>${esc(fmtWhen(d.at))}${d.stale ? `<p class="warn-line" data-doc-stale>${esc(d.stale_text)}</p>` : ""}</td>`
        : `<td colspan="2">${esc(d.missing || "not written yet")}${d.command ? cmdFold(`<code>${esc(d.command)}</code>`) : ""}</td>`) + `</tr>`).join("")}</table>`;
  }

  // the whole page body, the sections in the CLI's order
  function pageHtml(d, st) {
    const sec = (key, body) => `<section class="card" data-results-section="${esc(key)}"><h3${key === "curve" ? ' tabindex="-1"' : ""}>${esc(d.headings[key])}</h3>${body}</section>`;
    return (d.blinded_review ? `<p data-results-blinded>${esc(d.blinded_review.line)}</p>` : "")   // review packages, once
      + headingHtml(d.heading)
      + sec("agreement", linesHtml(d.agreement.lines))
      + sec("comparisons", d.comparisons.lines.map(l => `<p>${esc(l)}</p>`).join(""))
      + sec("recall", recallHtml(d.recall))
      + sec("curve", curveHtml(d.curve))
      + sec("counts", countsHtml(d.counts, d.count_lines))
      + sec("reference_sets", refsHtml(d.reference_sets, st, d))
      + sec("documents", (d.documents_stale_line ? `<p class="warn-line" data-docs-stale>${esc(d.documents_stale_line)}</p>` : "") + docsHtml(d.documents));
  }
  // nothing screened: the sentence in normal text and the way to the Screening page
  const nothingHtml = () => `<p data-results-nothing>${esc(NOTHING)}. Screen records on the <a href="#screen">Screening page</a> first.</p>`;

  function fillSelects(parts, d) {
    const ch = d.choices;
    parts.protocol.innerHTML = ch.protocols.map(p => opt(p, p, p === d.protocol)).join(""); parts.protocol.value = d.protocol;
    parts.set.innerHTML = ch.sets.map(s => opt(s.result_set, `set ${s.result_set}${s.label ? " " + s.label : ""}${s.active ? " (active)" : ""}`, s.result_set === d.result_set)).join("");
    parts.set.value = String(d.result_set);
    const samples = ch.samples.some(s => s.name === d.sample) ? ch.samples : [...ch.samples, { name: d.sample, kind: null }];
    parts.sample.innerHTML = samples.map(s => opt(s.name, s.kind === "purposive" ? `${s.name} (development sample)` : s.name, s.name === d.sample)).join("");
    parts.sample.value = d.sample;
  }
  // the query of GET /api/results, as the CLI's arguments: protocol, result_set, sample (empty ones left out)
  function query(c) {
    const qs = [["protocol", c.protocol], ["result_set", c.result_set], ["sample", c.sample]]
      .filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
    return qs.length ? "?" + qs.join("&") : "";
  }

  function controller(parts, deps) {
    let shown = null;
    async function load(choice) {
      let d;
      try { d = await deps.api(`/api/results${query(choice || {})}`); }
      catch (e) {
        shown = null;
        parts.body.innerHTML = e.message === NOTHING ? nothingHtml() : `<p class="bad">${esc(e.message)}</p>`;
        return null;
      }
      shown = d; fillSelects(parts, d); parts.body.innerHTML = pageHtml(d, st);
      if (parts.find) await cardLoaded();
      return d;
    }
    const current = () => ({ protocol: parts.protocol.value, result_set: parts.set.value, sample: parts.sample.value });
    // a new protocol starts at its active set; a set or sample keeps the other choices
    const changed = (which) => load(which === "protocol" ? { protocol: parts.protocol.value, sample: parts.sample.value } : current());
    // the shown set as the export routes' query: result_set only when it is not the active set
    const setQuery = () => query({ protocol: shown.protocol, result_set: shown.result_set === shown.active_set ? null : shown.result_set });
    // the documents section again after a file was written (the card keeps its choices and focus)
    async function reloadDocs() {
      try {
        const d = await deps.api(`/api/results${query({ protocol: shown.protocol, result_set: shown.result_set, sample: shown.sample })}`);
        shown = d; const sec = find('[data-results-section="documents"]');
        if (sec) sec.innerHTML = `<h3>${esc(d.headings.documents)}</h3>${docsHtml(d.documents)}`;
      } catch (e) { /* the written file is linked in the card's message */ }
    }
    const find = (sel) => (parts.find ? parts.find(sel) : null);
    const say = (name, html) => { const e = find(`[data-export-${name}]`); if (e) e.innerHTML = html; };
    const bad = (e) => `<span class="bad">${esc(e.message)}</span>`;

    // the card's choices (design D2): a filter change re-lists the records and ticks them all again
    let st = blankChoice(), rows = [], seq = 0, timer = null;
    const picked = () => rows.filter(r => !st.unticked.includes(r.record_id)).map(r => r.record_id);
    const noneTicked = () => rows.length && st.unticked.length && !picked().length;
    function selectionQuery() {
      const qs = [["preset", st.preset], ["pathway", st.pathways.join(",")], ["pattern", st.patterns.join(",")],
        ["final_only", st.final_only ? "true" : ""], ["records", st.unticked.length ? picked().join(",") : ""],
        ["format", st.formats.join(",")], ["abstracts", st.abstracts ? "" : "false"]];
      return qs.filter(([, v]) => v).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join("");
    }
    const withSet = (rest) => { const b = setQuery(); return b ? b + rest : rest.replace("&", "?"); };
    function blocked() {
      if (!st.formats.length) return "Tick at least one format to export.";
      if (noneTicked()) return "No record is ticked; tick at least one to export.";
      return null;
    }
    // the package's selection part follows the ticks too: none ticked writes no package
    const packageBlocked = () => (st.add.includes("selection") && noneTicked()
      ? "No record is ticked; tick at least one, or leave the selection out of the package." : null);
    // the count line, re-asked on each change; the last answer wins
    async function count(relist) {
      if (!shown) return null;
      const go = find("[data-export-go]"), stop = blocked();
      if (go) go.disabled = !!stop;
      if (stop) { say("count", esc(stop)); say("breakdown", ""); return null; }
      const n = ++seq;
      try {
        const r = await deps.api(`/api/outputs/selection${withSet(selectionQuery())}`);
        if (n !== seq) return null;
        if (relist) { rows = r.records; say("records", recordsHtml(rows, st.unticked)); }
        say("count", esc(r.n ? r.count_line : r.message) + (r.n ? ` · ${esc(r.file.split("/").pop())}` : ""));
        // per pathway and per pattern of the first two reads, as `wider export selection --dry-run` prints them
        say("breakdown", r.n ? `${esc(r.pathway_line)}<br>${esc(r.pattern_line)}` : "");
        return r;
      } catch (e) { if (n === seq) say("count", bad(e)); return null; }
    }
    // the package's contents and counts, listed before it is written (design D8)
    async function packageList() {
      if (!shown) return null;
      const go = find("[data-export-package]"), stop = packageBlocked();
      if (go) go.disabled = !!stop;
      if (stop) { say("package-list", esc(stop)); return null; }
      try {
        const r = await deps.api(`/api/outputs/selection${withSet(packageQuery() + "&package=true")}`);
        say("package-list", r.ready === false ? esc(r.message) : packageListHtml(r)); return r;   // exports wait: the sentence
      } catch (e) { say("package-list", bad(e)); return null; }
    }
    function packageQuery() {
      const sel = st.add.includes("selection") ? selectionQuery().replace(/&format=[^&]*/, "").replace(/&abstracts=false/, "") : "";
      return sel + (st.add.length ? `&add=${encodeURIComponent(st.add.join(","))}` : "") + `&format=${encodeURIComponent(st.pkg_formats.join(",") || "ris")}`
        + (st.abstracts ? "" : "&abstracts=false");
    }
    const later = (fn) => { if (!deps.wait) return fn(); clearTimeout(timer); return new Promise(ok => { timer = setTimeout(() => ok(fn()), deps.wait); }); };
    const flip = (list, v, on) => (on ? [...list.filter(x => x !== v), v] : list.filter(x => x !== v));
    const order = (list, all) => all.map(([v]) => v).filter(v => list.includes(v));
    // one tick box or the select changed: `name` is its data-export-field
    function choose(name, value, on) {
      if (name === "preset") st.preset = value;
      else if (name === "pathway") st.pathways = order(flip(st.pathways, value, on), PATHWAYS);
      else if (name === "pattern") st.patterns = order(flip(st.patterns, value, on), PATTERNS);
      else if (name === "final_only") st.final_only = !!on;
      else if (name === "record") st.unticked = flip(st.unticked, Number(value), !on);
      else if (name === "format") st.formats = order(flip(st.formats, value, on), FORMATS);
      else if (name === "abstracts") st.abstracts = !!on;
      else if (name === "add") st.add = order(flip(st.add, value, on), PACKAGE_ADD);
      else if (name === "pkg_format") st.pkg_formats = order(flip(st.pkg_formats, value, on), FORMATS);
      else return null;
      const filter = ["preset", "pathway", "pattern", "final_only"].includes(name);
      if (filter) st.unticked = [];
      if (name === "add" || name === "pkg_format") return later(packageList);
      return later(async () => { const r = await count(filter); if (st.add.includes("selection") || name === "abstracts") await packageList(); return r; });
    }
    // after the page is drawn: the records, the count line and the package list for the shown set
    async function cardLoaded() { st.unticked = []; await count(true); await packageList(); }
    // one write at a time: a second press (a double-click) while a write runs sends nothing
    let busy = false;
    async function post(rest, show) {
      if (busy) return null;
      busy = true; say("msg", "writing…");
      try { const r = await deps.api(`/api/outputs/selection${withSet(rest)}`, { method: "POST" }); say("msg", show(r)); await reloadDocs(); return r; }
      catch (e) { say("msg", bad(e)); return null; }
      finally { busy = false; }
    }
    const exportNow = () => (blocked() ? null : post(selectionQuery(), r => (r.written ? `${esc(r.count_line)}: ${fileLink(r.file)}` : esc(r.message))));
    const audit = () => post("&audit=true", r => `Audit CSV, ${esc(Number(r.n).toLocaleString("en"))} records: ${fileLink(r.file)}`);
    const writePackage = () => (packageBlocked() ? null : post(packageQuery() + "&package=true", r => `Export package, ${esc(r.files.length)} files: ${fileLink(r.file)}`));
    // Every set at once (the CLI's `wider export references --all`): the index as a table, then the documents again
    async function writeAll() {
      if (!shown || busy) return null;
      busy = true; say("msg", "writing…");
      try {
        const r = await deps.api(`/api/outputs/references?all=true${setQuery().replace("?", "&")}`, { method: "POST" });
        const n = r.sets.filter(s => s.files.length).length;
        say("msg", `${esc(n)} ${n === 1 ? "set" : "sets"} written: ${fileLink(r.index)}`); say("index", indexHtml(r));
        await reloadDocs(); return r;
      } catch (e) { say("msg", bad(e)); return null; }
      finally { busy = false; }
    }
    // **Estimate recall again** on a stale chart (design D10): the Recall page's GET /api/recall for the shown
    // protocol, set and sample (one look on record), its choice asked the same way, then the dashboard again
    async function estimateAgain(choice) {
      const a = shown && shown.curve && shown.curve.estimate_again;
      if (!a) return null;
      const msg = (html) => { const e = find("[data-curve-msg]"); if (e) e.innerHTML = html; };
      const q = [["protocol", a.protocol], ["sample", a.sample], ["result_set", a.result_set], ["choice", choice]]
        .filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
      msg("estimating…");
      try {
        const r = await deps.api(`/api/recall?${q}`);
        if (r.labels_incomplete) { msg(esc(r.notice)); return null; }   // label-completion-and-sealing D4
        if (r.choice_needed) {
          const c = deps.ask ? await deps.ask(r.notice) : null;
          if (c) return estimateAgain(c);
          msg(esc(deps.ask ? "Nothing was done." : r.notice)); return null;
        }
        const d = await load({ protocol: shown.protocol, result_set: shown.result_set, sample: shown.sample });
        // the button is gone with the redraw: focus moves to the chart's heading, not to the page top
        const h = find('[data-results-section="curve"] h3');
        if (h && h.focus) h.focus();
        return d;
      } catch (e) { msg(bad(e)); return null; }
    }
    // Import adjudicator's decisions (design D13): the chosen file is posted with dry_run first; "Import" posts the same
    // file into the set shown, then the page is drawn again with the new active set and the result's lines
    const adj = { file: null, adjudicator: "", column: "adjudication", counted: false, ask: 0 };
    const adjGo = (on) => { const b = find("[data-adj-go]"); if (b) b.disabled = !on; };
    async function adjPost(dryRun) {
      const fd = new FormData();
      fd.append("file", adj.file, adj.file.name || "file.csv"); fd.append("protocol", shown.protocol);
      fd.append("result_set", String(shown.result_set)); fd.append("adjudicator", adj.adjudicator.trim());
      fd.append("column", adj.column.trim() || "adjudication"); fd.append("dry_run", dryRun ? "true" : "false");
      return deps.api("/api/screen/adjudication", { method: "POST", body: fd });
    }
    // a file chosen, or the adjudicator or column changed: count again; nothing is written
    async function adjCheck() {
      adj.counted = false; adjGo(false);
      if (!shown || !adj.file) return null;
      const msg = (html) => { const e = find("[data-adj-msg]"); if (e) e.innerHTML = html; };
      msg("counting…");
      const ask = ++adj.ask;   // only the newest count may show its lines or turn Import on
      try { const r = await adjPost(true); if (ask !== adj.ask) return null; msg(adjudicationLinesHtml(r)); adj.counted = r.counts.imported > 0; adjGo(adj.counted); return r; }
      catch (e) { if (ask === adj.ask) msg(bad(e)); return null; }
    }
    // typing in a field: the shown count is stale until the field is left
    const adjEditing = () => { adj.counted = false; adjGo(false); };
    const adjChoose = (name, value) => { if (name === "file") adj.file = value; else adj[name] = value || ""; return adjCheck(); };
    async function adjImport() {
      if (!shown || !adj.file || !adj.counted || busy) return null;
      busy = true; adjGo(false);
      const msg = (html) => { const e = find("[data-adj-msg]"); if (e) e.innerHTML = html; };
      msg("importing…");
      try {
        const r = await adjPost(false);
        adj.file = null; adj.counted = false;
        await load({ protocol: shown.protocol, sample: shown.sample });   // the new set is active now
        msg(adjudicationLinesHtml(r)); return r;
      } catch (e) { msg(bad(e)); adjGo(true); return null; }
      finally { busy = false; }
    }
    return { load, changed, choose, estimateAgain, cardLoaded, exportNow, audit, writePackage, writeAll, choice: () => st,
      selectionQuery, shown: () => shown, adjChoose, adjEditing, adjImport };
  }

  return { controller, pageHtml, exportCardHtml, adjudicationCardHtml, adjudicationLinesHtml, recordsHtml, indexHtml, OLDER, ABSTRACTS, chartSvg, curveHtml, bindTip, recallHtml, linesHtml, query, nothingHtml };
})();

// the page itself (index.html section page-results); app.js calls loadResults when #results opens
(function () {
  if (typeof document === "undefined" || !document.querySelector("#page-results")) return;
  const q = (s) => document.querySelector(s);
  const parts = { protocol: q("#rs-protocol"), set: q("#rs-set"), sample: q("#rs-sample"), body: q("#rs-body") };
  parts.find = (sel) => parts.body.querySelector(sel);
  const c = Results.controller(parts, { api, wait: 250, ask: (n) => (typeof askSampleChoice === "function" ? askSampleChoice(n) : null) });
  Results.bindTip(parts.body);
  let first = true;
  window.loadResults = () => { const go = first ? c.load({}) : c.load({ protocol: parts.protocol.value, result_set: parts.set.value, sample: parts.sample.value }); first = false; return go; };
  parts.protocol.onchange = () => c.changed("protocol");
  parts.set.onchange = () => c.changed("set");
  parts.sample.onchange = () => c.changed("sample");
  parts.body.addEventListener("change", (e) => {
    const ds = e.target.dataset || {};
    if (e.target.hasAttribute && e.target.hasAttribute("data-adj-file")) return c.adjChoose("file", e.target.files && e.target.files[0]);
    if (ds.adjField) return c.adjChoose(ds.adjField, e.target.value);
    const f = ds.exportField;
    if (f) c.choose(f, e.target.value, e.target.type === "checkbox" ? e.target.checked : true);
  });
  parts.body.addEventListener("input", (e) => { if ((e.target.dataset || {}).adjField) c.adjEditing(); });
  parts.body.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest("[data-adj-go]")) return c.adjImport();
    const b = e.target.closest && e.target.closest("[data-export-go],[data-export-audit],[data-export-package],[data-export-all],[data-curve-again]");
    if (!b) return;
    if (b.hasAttribute("data-curve-again")) c.estimateAgain();
    else if (b.hasAttribute("data-export-go")) c.exportNow();
    else if (b.hasAttribute("data-export-audit")) c.audit();
    else if (b.hasAttribute("data-export-package")) c.writePackage();
    else c.writeAll();
  });
})();
