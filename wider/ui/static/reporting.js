// PRISMA reporting (change prisma-reporting-assistant, design D14, D21, D24): the PRISMA 2020 and PRISMA-S checklists
// and the flow diagram for one protocol and result set, from the routes `wider report prisma ...` twins
// (/api/reporting/...). Each item shows what PRISMA asks, what WIDER recorded, what is still missing, the related items
// of the other checklist and the researcher's own text, kept apart from the records. The builders return HTML strings
// only (every value through `esc`, common.js); the controller is given its parts and api, so tests run it under Node
// with fakes. The form's values live in the controller (`edit`), so drawing never loses what was typed. Sentences are
// normal text, black or red, never grey; "outdated" and inconsistency lines are red and said once.
const Reporting = (() => {
  const TITLES = { prisma_2020: "PRISMA 2020", prisma_s: "PRISMA-S" };
  const TABS = [["prisma_2020", "PRISMA 2020"], ["prisma_s", "PRISMA-S"], ["flow", "Flow diagram"]];
  const STATUSES = ["Automatically documented", "Partially documented", "Researcher input needed", "Not applicable"];
  const TO_CHECK = "To check";
  const ENTERED = ["registers", "automation_removed", "not_retrieved", "studies", "reports"];
  const NA_STATUS = "Not applicable";
  const opt = (v, label, on) => `<option value="${esc(v)}"${on ? " selected" : ""}>${esc(label)}</option>`;
  const fileLink = (path, text) => `<a href="/api/outputs/file?path=${encodeURIComponent(path)}" download>${esc(text || path)}</a>`;
  const red = (s, attr) => `<p class="warn-line"${attr ? " " + attr : ""}>${esc(s)}</p>`;
  const linesList = (lines) => `<ul>${(lines || []).map(l => `<li>${esc(l.text)} (${esc(l.source)})</li>`).join("")}</ul>`;
  const itemName = (cl, no) => `${TITLES[cl]} item ${no}`;
  const refKey = (cl, no) => `${cl.replace("_", "-")}:${no}`;   // "prisma-s:13", as `edit --from-item` takes it
  const statusText = (r) => r.status + (r.wider_status && r.status !== r.wider_status ? ` (WIDER: ${r.wider_status})` : "");
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  function tabsHtml(tab) {
    return `<div class="page-tabs" role="tablist" aria-label="PRISMA reporting">${TABS.map(([k, label]) =>
      `<button type="button" role="tab" id="rp-tabbtn-${k}" data-rp-tab="${k}" aria-selected="${k === tab}"`
      + ` tabindex="${k === tab ? 0 : -1}" aria-controls="rp-tab">${esc(label)}</button>`).join("")}</div>`;
  }

  // the counts of the four statuses and of "To check", as `status --json` counted by status
  function counts(rows) {
    const c = Object.fromEntries(STATUSES.map(s => [s, 0]));
    c[TO_CHECK] = 0;
    for (const r of rows || []) { c[r.status] = (c[r.status] || 0) + 1; if (r.to_check) c[TO_CHECK] += 1; }
    return c;
  }
  const passes = (r, filter) => filter === "all" || (filter === TO_CHECK ? !!r.to_check : r.status === filter);

  function filtersHtml(rows, filter) {
    const c = counts(rows), all = (rows || []).length;
    const b = (key, label, n) => `<button type="button" class="secondary" data-rp-filter="${esc(key)}" aria-pressed="${filter === key}">`
      + `${esc(label)} <span class="tb-count">${esc(n)}</span></button>`;
    return `<div class="row" role="group" aria-label="Show items">${b("all", "All", all)}`
      + STATUSES.map(s => b(s, s, c[s])).join("") + b(TO_CHECK, TO_CHECK, c[TO_CHECK]) + "</div>";
  }

  // the rows under the official section headings, in PRISMA order
  function tableHtml(rows, filter, open) {
    const shown = (rows || []).filter(r => passes(r, filter));
    if (!shown.length) return "<p>No item has this status.</p>";
    const out = [], head = "<tr><th>Item</th><th>Topic</th><th>WIDER status</th><th>Your text</th></tr>";
    let section = null;
    for (const r of shown) {
      if (r.section !== section) { section = r.section; out.push(`<tr><th colspan="4" scope="colgroup">${esc(section)}</th></tr>`); }
      const on = open === String(r.no), yt = r.your_text === "outdated" || r.your_text === "unreadable";
      const gaps = r.gaps_left ? `; ${plural(r.gaps_left, "gap")} in brackets` : "";
      out.push(`<tr data-rp-row="${esc(r.no)}"${on ? ' class="on"' : ""}><td><button type="button" class="link" data-rp-open="${esc(r.no)}"`
        + ` aria-expanded="${on}">${esc(r.no)}</button></td><td>${esc(r.topic)}</td><td>${esc(statusText(r))}</td>`
        + `<td${yt ? ' class="warn-line"' : ""}>${esc(r.your_text_label || r.your_text)}${esc(gaps)}</td></tr>`);
    }
    return `<table class="rec-table" data-rp-table><thead>${head}</thead><tbody>${out.join("")}</tbody></table>`;
  }

  // one item (design D4): PRISMA asks, WIDER recorded, still missing, the related items, your text, the draft, the form
  function itemHtml(v, draft, form, ui) {
    const y = v.yours || {}, out = [];
    out.push(`<h3 tabindex="-1">${esc(itemName(v.checklist, v.no))}: ${esc(v.topic)} (${esc(v.section)})</h3>`);
    out.push(`<p data-rp-status>Status: ${esc(statusText(v))}</p>`);
    if (v.has_evidence) out.push(`<p>${esc(v.has_evidence)}</p>`);
    out.push(`<h4>PRISMA asks</h4><p data-rp-official>${esc(v.official)} (${esc(v.source)})</p>`
      + `<ul>${(v.elements || []).map(e => `<li>${esc(e.text)}</li>`).join("")}</ul>`);
    if (v.outside_sentence) out.push(`<p>${esc(v.outside_sentence)}</p>`);
    out.push("<h4>WIDER recorded</h4>" + ((v.evidence || []).length ? linesList(v.evidence) : "<p>nothing</p>"));
    out.push("<h4>Still missing</h4>" + ((v.missing || []).length ? `<ul>${v.missing.map(m =>
      `<li>${esc(m.text)}: ${esc(m.say)}${m.covered_by_you ? "; you marked as covered by your text" : ""}</li>`).join("")}</ul>` : "<p>nothing</p>"));
    if ((v.related || []).length) {
      out.push(`<h4 data-rp-related-head>${esc(v.related_heading)}</h4><ul data-rp-related>${v.related.map(x =>
        `<li><button type="button" class="link" data-rp-follow="${esc(x.checklist)}:${esc(x.no)}">${esc(itemName(x.checklist, x.no))}: `
        + `${esc(x.topic)} (${esc(x.status)})</button>${x.first_line ? `<br>Text: ${esc(x.first_line)} <button type="button" class="secondary"`
        + ` data-rp-start="${esc(x.checklist)}:${esc(x.no)}">Start from this text</button>` : ""}</li>`).join("")}</ul>`);
    }
    // your text as saved: what WIDER keeps apart from its records
    out.push("<h4>Your text</h4>");
    if (y.unreadable) out.push(red(y.unreadable));
    else if (y.not_applicable) out.push(`<p data-rp-na>Not applicable: ${esc(y.not_applicable.reason)}</p>`);
    if ((y.text || "").trim()) {
      out.push(`<p data-rp-saved>${esc(y.text).replace(/\n/g, "<br>")}</p><p>(${esc(y.your_text_label)}${y.saved_for ? `; saved for ${esc(y.saved_for)}` : ""})</p>`);
      if (y.gaps_left) out.push(`<p>Your text still has ${esc(plural(y.gaps_left, "gap"))} in brackets</p>`);
    } else if (!y.unreadable) out.push("<p>none</p>");
    if (y.where) out.push(`<p>Where reported: ${esc(y.where)}</p>`);
    const od = y.outdated;
    if (od) {
      out.push(red(od.reason || "Your text is outdated: WIDER's evidence for this item changed since you saved it.", "data-rp-outdated"));
      out.push(`<p>Saved with:</p>${linesList(od.saved)}<p>Now:</p>${linesList(od.current)}`);
      out.push((od.inconsistencies || []).map(l => red(l)).join(""));
    }
    if (draft) {
      out.push(`<h4>Draft from WIDER's records</h4><p>Not your text until you save it.</p><p data-rp-draft-text>${esc(draft.text).replace(/\n/g, "<br>")}</p>`
        + `<button type="button" class="secondary" data-rp-draft>Use the draft</button>`);
    }
    // the form: textarea, element ticks beside their text, where reported, the buttons
    const covers = new Set(form.covers || []);
    out.push(`<h4>Write or change your text</h4><label class="block">Your text for ${esc(itemName(v.checklist, v.no))}`
      + `<textarea rows="5" data-rp-field="text">${esc(form.text)}</textarea></label>`);
    if ((v.elements || []).length) {
      out.push(`<fieldset><legend>Your text covers</legend>${v.elements.map(e => `<label class="block"><input type="checkbox"`
        + ` data-rp-field="covers" value="${esc(e.key)}"${covers.has(e.key) ? " checked" : ""}> ${esc(e.text)}</label>`).join("")}</fieldset>`);
    }
    out.push(`<label class="block">Where reported <input type="text" data-rp-field="where" value="${esc(form.where)}"></label>`);
    const hasText = (y.text || "").trim() || (y.covers || []).length || y.where;
    out.push(`<div class="row"><button type="button" class="secondary" data-rp-save>Save</button>`
      + (od ? '<button type="button" class="secondary" data-rp-keep>Keep my text</button>' : "")
      + (hasText ? '<button type="button" class="secondary" data-rp-clear>Clear my text</button>' : "") + "</div>");
    // not applicable, with its reason; the button waits for a reason (`na` refuses without one)
    if (y.not_applicable) out.push('<div class="row"><button type="button" class="secondary" data-rp-na-clear>Remove the not-applicable mark</button></div>');
    else {
      out.push(`<div class="row"><label class="inline">Reason <input type="text" data-rp-field="reason" value="${esc(form.reason)}"></label>`
        + `<button type="button" class="secondary" data-rp-na-go${(form.reason || "").trim() ? "" : " disabled"}>Not applicable</button></div>`);
    }
    out.push(`<p data-rp-msg role="status">${ui.msg || ""}</p>`);
    return `<div data-rp-item>${out.join("")}</div>`;
  }

  // the flow diagram tab (design D21): the preview, the boxes with their source, number fields for the boxes WIDER
  // does not record, the reason rows, the checks in red, said once
  function flowHtml(st, form, ui) {
    const out = [];
    out.push(st.message ? `<p>${esc(st.message)}</p>` : `<h3>PRISMA 2020 flow, ${esc(st.protocol)} set ${esc(st.result_set)}</h3>`);
    if (st.svg) out.push(`<div class="rp-flow-svg" role="img" aria-label="PRISMA 2020 flow diagram, as the export draws it">${st.svg}</div>`);
    const entered = new Set(ENTERED);
    out.push(`<table class="rec-table" data-rp-boxes><thead><tr><th>Box</th><th>Count</th><th>Source</th></tr></thead><tbody>${(st.boxes || []).map(b => {
      const cell = entered.has(b.name)
        ? `<input type="text" inputmode="numeric" data-rp-count="${esc(b.name)}" aria-label="${esc(b.label)}" value="${esc(form.counts[b.name] == null ? "" : form.counts[b.name])}">`
        : esc(b.text);
      return `<tr><td>${esc(b.label)}</td><td>${cell}${b.source === "researcher" ? "†" : ""}</td><td>${esc(b.source)}</td></tr>`;
    }).join("")}</tbody></table>`);
    if ((st.boxes || []).some(b => b.source === "researcher")) out.push("<p>† entered by the researcher</p>");
    out.push(`<fieldset><legend>Reports excluded, with reasons</legend>${form.excluded.map((x, i) =>
      `<div class="row"><label class="inline">Reason <input type="text" data-rp-ex="${i}:reason" value="${esc(x.reason)}"></label>`
      + `<label class="inline">Number <input type="text" inputmode="numeric" data-rp-ex="${i}:n" value="${esc(x.n)}"></label></div>`).join("")}`
      + '<button type="button" class="secondary" data-rp-ex-add>Add a reason</button></fieldset>');
    if (st.other_sets) out.push(`<p data-rp-other-sets>${esc(st.other_sets)}</p>`);
    if (st.unreadable) out.push(red("could not read your flow counts for this set"));
    out.push((st.checks || []).map(l => red(l, "data-rp-check")).join(""));
    if (st.exports_stale) out.push(red(st.exports_stale, "data-rp-exports-stale"));
    // a box that is new since the save (a platform imported later), or gone now, has no count on that side
    const was = n => (n == null ? "not in the diagram" : n);
    if (st.outdated) out.push(`<ul>${(st.outdated.changed || []).map(c => `<li>${esc(c.label)}: ${esc(was(c.saved))} when you saved, ${esc(was(c.now))} now</li>`).join("")}</ul>`);
    out.push('<div class="row"><button type="button" class="secondary" data-rp-flow-save>Save counts</button>'
      + '<button type="button" class="secondary" data-rp-flow-clear>Clear my counts</button></div>');
    out.push(`<p data-rp-msg role="status">${ui.msg || ""}</p>`);
    return `<div data-rp-flow>${out.join("")}</div>`;
  }

  // the export's result: its message, then every file written, the Word files with a download link
  function exportHtml(r) {
    return `<p data-rp-export-msg tabindex="-1">${esc(r.message)}</p><ul>${(r.files || []).map(f =>
      `<li>${fileLink(f, f.replace(/^exports\//, ""))}</li>`).join("")}</ul>`;
  }

  function controller(parts, deps) {
    const find = (sel) => (parts.find ? parts.find(sel) : null);
    // a refusal is said at once (role alert), as it appears in place of the earlier line
    const bad = (e) => `<span class="bad" role="alert">${esc(e.message)}</span>`;
    const undoOffer = (id, text) => { const f = deps.offerUndo || (typeof window !== "undefined" && window.offerUndo); if (id && f) f(id, text); };
    const st = { tab: "prisma_2020", protocol: null, set: null, filter: "all", open: null, rows: {}, head: {}, choices: null,
      item: null, draft: null, form: { text: "", covers: [], where: "", reason: "" }, fromItem: null, flow: null,
      flowForm: { counts: {}, excluded: [] }, exported: null, msg: "", error: null };
    const q = () => {
      const a = [];
      if (st.protocol) a.push(`protocol=${encodeURIComponent(st.protocol)}`);
      if (st.set != null && st.set !== "") a.push(`set=${encodeURIComponent(st.set)}`);
      return a.length ? "?" + a.join("&") : "";
    };
    const target = () => ({ protocol: st.protocol, set: st.set == null || st.set === "" ? null : Number(st.set) });

    function fillSelects() {
      const ch = st.choices || { protocols: [], sets: [] };
      parts.protocol.innerHTML = ch.protocols.length ? ch.protocols.map(p => opt(p, p, p === st.protocol)).join("") : opt("", "no screening yet", true);
      parts.set.innerHTML = ch.sets.length ? ch.sets.map(s => opt(s.result_set, `set ${s.result_set}${s.label ? ": " + s.label : ""}${s.active ? " (active)" : ""}`,
        Number(s.result_set) === Number(st.set))).join("") : opt("", "no screening yet", true);
      parts.protocol.disabled = !ch.protocols.length; parts.set.disabled = !ch.sets.length;
    }

    function draw() {
      const out = [tabsHtml(st.tab), '<div id="rp-tab" role="tabpanel" aria-labelledby="rp-tabbtn-' + st.tab + '">'];
      if (st.error) out.push(`<p data-rp-error>${esc(st.error)}</p>`);
      else if (st.tab === "flow") { if (st.flow) out.push(flowHtml(st.flow, st.flowForm, st)); }
      else {
        const rows = st.rows[st.tab] || [];
        out.push(`<p data-rp-relate>${esc((st.head[st.tab] || {}).relate || "")}</p>`, filtersHtml(rows, st.filter),
          tableHtml(rows, st.filter, st.open && st.open.checklist === st.tab ? String(st.open.no) : null));
        if (st.item && st.open && st.open.checklist === st.tab) out.push(itemHtml(st.item, st.draft, st.form, st));
      }
      out.push("</div>", `<div data-rp-export>${!st.exported ? "" : st.exported.error ? `<p>${bad(st.exported.error)}</p>` : exportHtml(st.exported)}</div>`);
      parts.body.innerHTML = out.join("");
    }
    const say = (html) => { st.msg = html; const m = find("[data-rp-msg]"); if (m) m.innerHTML = html; else draw(); };

    async function loadRows(cl) {
      const r = await deps.api(`/api/reporting/${cl}${q()}`);
      st.rows[cl] = r.rows; st.head[cl] = { title: r.title, relate: r.relate };
      st.protocol = r.protocol; st.set = r.result_set; st.choices = r.choices;
      return r;
    }
    async function loadItem() {
      const { checklist, no } = st.open;
      const r = await deps.api(`/api/reporting/${checklist}/${encodeURIComponent(no)}${q()}`);
      const y = r.yours || {};
      st.item = r; st.draft = r.draft || null; st.fromItem = null;
      st.form = { text: y.text || "", covers: [...(y.covers || [])], where: y.where || "", reason: "" };
      return r;
    }
    async function loadFlow() {
      if (!st.choices) await loadRows("prisma_2020");
      const r = await deps.api(`/api/reporting/flow${q()}`);
      st.flow = r;
      st.flowForm = { counts: Object.fromEntries(ENTERED.map(k => [k, r.entered && r.entered[k] != null ? String(r.entered[k]) : ""])),
        excluded: [...(r.excluded || []).map(x => ({ reason: x.reason, n: String(x.n) })), { reason: "", n: "" }] };
      return r;
    }
    // the page for the chosen tab, protocol and set; the open item again
    async function load(opts = {}) {
      if ("protocol" in opts) { st.protocol = opts.protocol || null; st.set = null; }
      if ("set" in opts) st.set = opts.set === "" ? null : opts.set;
      if (opts.tab) st.tab = opts.tab;
      st.error = null; st.msg = "";   // the last action's line; itemPost and the flow buttons say theirs again
      try {
        if (st.tab === "flow") await loadFlow();
        else {
          await loadRows(st.tab);
          if (st.open && st.open.checklist === st.tab) await loadItem(); else st.item = null;
        }
      } catch (e) { st.error = e.message; }
      fillSelects(); draw();
      return st;
    }
    const tab = (key) => { st.msg = ""; return load({ tab: key }); };
    function filter(key) {
      st.filter = key;
      const rows = st.rows[st.tab] || [], open = st.open && st.open.checklist === st.tab && rows.find(r => String(r.no) === String(st.open.no));
      if (open && !passes(open, key)) { st.open = null; st.item = null; }
      draw();
      return st;
    }
    async function open(no) {
      st.open = { checklist: st.tab, no: String(no) }; st.msg = "";
      try { await loadItem(); } catch (e) { st.msg = bad(e); }
      draw(); focusItem();
      return st.item;
    }
    // a related item's link: its tab, the item open, the protocol and set kept, the shared filter back to all
    async function follow(cl, no) {
      st.tab = cl; st.open = { checklist: cl, no: String(no) }; st.msg = ""; st.error = null;
      try {
        st.filter = "all";
        await loadRows(cl);
        await loadItem();
      } catch (e) { st.error = e.message; }
      fillSelects(); draw(); focusItem();
      return st;
    }
    function focusItem() { const h = find("[data-rp-item] h3"); if (h && h.focus) h.focus(); }
    // after a redraw the focus goes back to the control that was pressed, or to the one that took its place (Not
    // applicable and Remove the mark swap; Keep and Clear go away), and a line below the fold is brought into view
    function refocus(sel) {
      const n = find(sel); if (n && n.focus) n.focus();
      const m = find("[data-rp-msg]"); if (m && m.scrollIntoView) m.scrollIntoView({ block: "nearest" });
    }
    // "Start from this text": the related item's text fills the text box; nothing is saved until Save
    async function startFrom(cl, no) {
      try {
        const r = await deps.api(`/api/reporting/${cl}/${encodeURIComponent(no)}${q()}`);
        const text = (r.yours || {}).text || "";
        st.form.text = text; st.fromItem = { key: refKey(cl, no), text };
        st.msg = esc(`The text of ${itemName(cl, no)} is in the box. Nothing is saved until you press Save.`);
        draw();
        const t = find('[data-rp-field="text"]'); if (t && t.focus) t.focus();
      } catch (e) { say(bad(e)); }
      return st.form;
    }
    // a field of the form changed; nothing is drawn, so the focus and the caret stay
    function edit(name, value, on) {
      if (name === "covers") st.form.covers = on ? [...st.form.covers.filter(k => k !== value), value] : st.form.covers.filter(k => k !== value);
      else if (name === "count") st.flowForm.counts[value[0]] = value[1];
      else if (name === "ex") { const [i, k] = value[0].split(":"); st.flowForm.excluded[Number(i)][k] = value[1]; }
      else st.form[name] = value;
      if (name === "reason") { const b = find("[data-rp-na-go]"); if (b) b.disabled = !String(value).trim(); }
    }
    function addRow() { st.flowForm.excluded.push({ reason: "", n: "" }); draw(); }

    let busy = false;
    async function post(path, body, done) {
      if (busy) return null;
      busy = true;
      try {
        const r = await deps.api(path, { method: "POST", body: JSON.stringify(body) });
        st.msg = esc(r.changed === false ? "Nothing changed." : done);
        if (r.changed !== false) undoOffer(r.change_id, done);
        return r;
      } catch (e) { st.msg = bad(e); draw(); return null; }
      finally { busy = false; }
    }
    async function itemPost(rest, body, done, focusSel = "[data-rp-save]") {
      const { checklist, no } = st.open;
      const r = await post(`/api/reporting/${checklist}/${encodeURIComponent(no)}/${rest}`, body, `${itemName(checklist, no)}: ${done}`);
      if (!r) { refocus(focusSel); return null; }
      const msg = st.msg;
      await load(); st.msg = msg; draw(); refocus(focusSel);
      return r;
    }
    const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x));
    // Save sends what changed, as `wider report prisma edit` maps its options: text only when it differs from the
    // saved text (or `from_item` when the box still holds a related item's text), covers and where only when changed
    function save() {
      if (!st.item) return null;
      const y = st.item.yours || {}, f = st.form, body = target();
      if (st.fromItem && f.text === st.fromItem.text) body.from_item = st.fromItem.key;
      else if (f.text !== (y.text || "")) body.text = f.text;
      if (!sameSet(f.covers, y.covers || [])) body.covers = [...f.covers];
      if (f.where !== (y.where || "")) body.where = f.where;
      if (!("text" in body || "from_item" in body || "covers" in body || "where" in body)) { say(esc("Nothing changed.")); return null; }
      return itemPost("text", body, "text" in body || "from_item" in body ? "text saved" : "covers" in body ? "text saved" : "location saved");
    }
    const useDraft = () => (st.draft ? itemPost("text", { ...target(), from_draft: true }, "text saved from the draft") : null);
    const keep = () => itemPost("text", { ...target(), keep: true }, "text kept for the current evidence");
    const clearText = () => itemPost("clear", {}, "text cleared");
    function na() {
      const reason = (st.form.reason || "").trim();
      return reason ? itemPost("na", { reason }, "marked not applicable", "[data-rp-na-clear]") : null;
    }
    const naClear = () => itemPost("na", { clear: true }, "not-applicable mark removed", '[data-rp-field="reason"]');

    // the flow tab's Save: every field, an empty one as a removal (`flow --count NAME=`), and the reason rows
    async function saveFlow() {
      const body = { ...target(), counts: { ...st.flowForm.counts }, excluded: st.flowForm.excluded.map(x => ({ reason: x.reason, n: x.n })) };
      const r = await post("/api/reporting/flow", body, "PRISMA 2020 flow: counts saved");
      if (r && r.flow) { const msg = st.msg; st.flow = r.flow; await loadFlow(); st.msg = msg; }
      draw(); refocus("[data-rp-flow-save]");
      return r;
    }
    async function clearFlow() {
      const r = await post("/api/reporting/flow/clear", target(), "PRISMA 2020 flow: counts cleared");
      if (r) { const msg = st.msg; await loadFlow(); st.msg = msg; }
      draw(); refocus("[data-rp-flow-clear]");
      return r;
    }
    // "Export checklists" (the top bar's main button): the message and every file written
    async function exportNow() {
      if (busy) return null;
      busy = true;
      const box = () => find("[data-rp-export]");
      try {
        if (box()) box().innerHTML = "<p>writing…</p>";
        const r = await deps.api("/api/reporting/export", { method: "POST", body: JSON.stringify(target()) });
        st.exported = r;
        if (box()) box().innerHTML = exportHtml(r); else draw();
        const m = find("[data-rp-export-msg]"); if (m && m.focus) m.focus();   // the result is far below the button
        return r;
      } catch (e) {
        st.exported = { error: e };
        if (box()) box().innerHTML = `<p>${bad(e)}</p>`; else draw();
        const b = box(); if (b && b.scrollIntoView) b.scrollIntoView({ block: "nearest" });
        return null;
      } finally { busy = false; }
    }
    return { load, tab, filter, open, follow, startFrom, edit, addRow, save, useDraft, keep, clearText, na, naClear,
      saveFlow, clearFlow, exportNow, state: () => st };
  }

  return { controller, tabsHtml, filtersHtml, tableHtml, itemHtml, flowHtml, exportHtml, counts, STATUSES, TO_CHECK };
})();

// the page itself (index.html section page-reporting); app.js calls loadReporting when #reporting opens
(function () {
  if (typeof document === "undefined" || !document.querySelector("#page-reporting")) return;
  const q = (s) => document.querySelector(s);
  const parts = { protocol: q("#rp-protocol"), set: q("#rp-set"), body: q("#rp-body") };
  parts.find = (sel) => parts.body.querySelector(sel);
  const c = Reporting.controller(parts, { api });
  // shell.js (Topbar) loads after this file, so the main button is set when the page opens, as dedup.js does
  window.loadReporting = () => {
    if (window.Topbar) Topbar.set("reporting", { main: { label: "Export checklists", onclick: () => c.exportNow() } });
    return c.load();
  };
  parts.protocol.onchange = () => c.load({ protocol: parts.protocol.value });
  parts.set.onchange = () => c.load({ set: parts.set.value });
  const pair = (s) => { const i = s.lastIndexOf(":"); return [s.slice(0, i), s.slice(i + 1)]; };
  parts.body.addEventListener("input", (e) => {
    const t = e.target, d = t.dataset || {};
    if (d.rpField && d.rpField !== "covers") c.edit(d.rpField, t.value);
    else if (d.rpCount) c.edit("count", [d.rpCount, t.value]);
    else if (d.rpEx) c.edit("ex", [d.rpEx, t.value]);
  });
  parts.body.addEventListener("change", (e) => {
    const t = e.target, d = t.dataset || {};
    if (d.rpField === "covers") c.edit("covers", t.value, t.checked);
  });
  parts.body.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("button");
    if (!b || b.disabled) return;
    const d = b.dataset;
    if (d.rpTab) c.tab(d.rpTab);
    else if (d.rpFilter) c.filter(d.rpFilter);
    else if (d.rpOpen) c.open(d.rpOpen);
    else if (d.rpFollow) c.follow(...pair(d.rpFollow));
    else if (d.rpStart) c.startFrom(...pair(d.rpStart));
    else if ("rpSave" in d) c.save();
    else if ("rpDraft" in d) c.useDraft();
    else if ("rpKeep" in d) c.keep();
    else if ("rpClear" in d) c.clearText();
    else if ("rpNaGo" in d) c.na();
    else if ("rpNaClear" in d) c.naClear();
    else if ("rpFlowSave" in d) c.saveFlow();
    else if ("rpFlowClear" in d) c.clearFlow();
    else if ("rpExAdd" in d) c.addRow();
  });
  // the tabs: arrow keys, Home and End move between them, as on the other pages' tab bars
  parts.body.addEventListener("keydown", (e) => {
    const t = e.target.closest && e.target.closest('[role="tab"]');
    if (!t) return;
    const keys = ["prisma_2020", "prisma_s", "flow"], i = keys.indexOf(t.dataset.rpTab);
    const j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: keys.length - 1 }[e.key];
    if (j == null) return;
    e.preventDefault();
    c.tab(keys[(j + keys.length) % keys.length]).then(() => { const n = parts.find(`[data-rp-tab="${keys[(j + keys.length) % keys.length]}"]`); if (n) n.focus(); });
  });
})();
