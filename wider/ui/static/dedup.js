// The Deduplication page (design-pass-a1-dedup): flow chart, list of pairs, the docked pair view. The match words and
// the marked differences are pure functions (D9), the same list as wider/dedup/engine.py match_word().
(function () {
  // lower case, accents dropped, punctuation to space, spaces collapsed
  function norm(s) {
    return String(s).toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "")
      .replace(/[^\p{L}\p{N}\s]|_/gu, " ").split(/\s+/).filter(Boolean).join(" ");
  }
  const words = (s) => norm(s).split(" ").filter(Boolean);

  // "same", "spelling", "nearly" (word-set Jaccard >= 0.8), "only_a", "only_b", "missing" or "differs"
  function matchWord(a, b) {
    a = a == null ? "" : String(a).trim();
    b = b == null ? "" : String(b).trim();
    if (!a && !b) return "missing";
    if (!b) return "only_a";
    if (!a) return "only_b";
    if (a === b) return "same";
    const na = norm(a), nb = norm(b);
    if (na === nb) return "spelling";
    const wa = new Set(na.split(" ").filter(Boolean)), wb = new Set(nb.split(" ").filter(Boolean));
    if (wa.size && wb.size) {
      let both = 0; wa.forEach((w) => { if (wb.has(w)) both++; });
      if (both / (wa.size + wb.size - both) >= 0.8) return "nearly";
    }
    return "differs";
  }

  // each side as HTML (owner walkthrough P10): words the other side lacks are wrapped in <mark class="diff"> (light pink,
  // with a dotted underline too, not colour alone), words both sides have in <mark class="same"> (light green); every piece of record text is escaped first, so it never becomes HTML
  function markDiff(a, b) {
    const side = (s, other) => {
      const keep = new Set(words(other));
      // a run of shared words (with the spaces between them) is one green mark; each differing word its own pink one
      let out = "", same = "", gap = "";
      const flush = () => { if (same) out += `<mark class="same">${esc(same)}</mark>`; same = ""; };
      for (const t of String(s ?? "").split(/(\s+)/)) {
        if (!t) continue;
        if (/^\s+$/.test(t)) { if (same) gap += t; else out += esc(t); continue; }
        const n = words(t);
        if (!n.length) { if (same) { same += gap + t; gap = ""; } else out += esc(t); continue; }   // "&" breaks no run
        if (!n.some((w) => !keep.has(w))) { same += gap + t; gap = ""; continue; }
        flush(); out += esc(gap); gap = "";
        out += `<mark class="diff">${esc(t)}</mark>`;
      }
      flush(); return out + esc(gap);
    };
    return { a: side(a, b), b: side(b, a) };
  }

  // ---------------------------------------------------------------- the page (D5-D8): pure HTML builders first
  const MATCH = { same: "Same", spelling: "Spelling differs", nearly: "Nearly the same", only_a: "Only in A",
                  only_b: "Only in B", missing: "Missing", differs: "Differs" };
  // the mark before the match word (aria-hidden: the word says it); its tone is set by the row's class
  const MATCH_SYM = { same: "✓", spelling: "≈", nearly: "≈", only_a: "◐", only_b: "◑", missing: "–", differs: "≠" };
  const matchCell = (w) => `<td class="dd-match"><span class="dd-sym" aria-hidden="true">${MATCH_SYM[w]}</span>${MATCH[w]}</td>`;
  const missing = '<span class="dd-miss">Missing</span>';
  const BOXES = [["imported", "Records imported"], ["removed", "Duplicates removed"], ["left", "Records left"], ["queue", "Pairs to decide"]];
  const STATUS_KEY = { open: "open", manual_same: "dup", manual_different: "notdup", manual_uncertain: "unsure" };
  const STATUS_WORD = { open: "Open", manual_same: "Duplicate", manual_different: "Not duplicate", manual_uncertain: "Unsure" };
  const DECIDED_AS = { duplicate: "manual_same", not_duplicate: "manual_different", uncertain: "manual_uncertain" };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;
  // a status chip: the shell's (word and aria-hidden icon); a stand-in keeps the word where shell.js is not loaded
  const chip = (key, text) => window.Status ? Status.chip(key, text) : `<span class="st-chip st-${esc(key)}"><span class="st-word">${esc(text)}</span></span>`;
  const when = (t) => fmtWhen(t);   // common.js
  const keyOf = (r) => r && r.raw_a != null ? `${r.raw_a}-${r.raw_b}` : (r && r.record_id != null ? `r${r.record_id}` : (r && r.a != null ? `${r.a}-${r.b}` : ""));

  function chipHtml(f) {
    if (!f.has_run) return chip("open", "Not run yet");
    if (f.to_decide > 0) return chip("wait", "Needs you");
    if (f.provisional) return chip("unsure", "Provisional");
    return chip("done", "Done");
  }
  // the box a page opens on: pairs to decide while any is open, else records left; before a run, records imported
  const defaultBox = (f) => (!f.has_run ? "imported" : (f.to_decide > 0 ? "queue" : "left"));

  // a box: its title and "Show ›" (or "✓ Showing" while pressed) on one line, the number with its words beside it
  function boxHtml(k, title, f, pressed) {
    const off = !f.has_run && k !== "imported";
    const n = off ? "–" : { imported: f.imported, removed: f.removed, left: f.left, queue: f.to_decide }[k];
    let sub = [];
    if (k === "imported") {
      sub.push(`from ${plural(f.files || 0, "database export")}`);
      if (f.imported_since_run) sub.push({ alert: true, text: `+${f.imported_since_run} more imported since, not checked for duplicates yet` });
      if (f.excluded_file_records > 0) sub.push(`file excluded: ${f.excluded_file_records}`);
    } else if (!off && k === "removed") sub.push(f.split_recorded ? `(${f.removed_auto} by WIDER, ${f.removed_manual} by your decisions)` : "split not recorded");
    else if (!off && k === "left") sub.push(f.provisional ? "provisional" : "final");
    else if (!off && k === "queue") sub.push(f.to_decide ? "waiting for you" : (f.unsure ? `${f.unsure} unsure` : "none open"));
    const wait = k === "queue" && !off && f.to_decide > 0;
    const show = pressed ? '<span class="dd-showing">✓ Showing</span>' : (off ? "" : `<span class="dd-show" aria-hidden="true">${k === "removed" ? "Check" : "Show"} ›</span>`);
    // D32 F10: records not checked yet are said in normal text colour, not grey
    const subs = sub.map((s, i) => [typeof s === "string" ? s : s.text, s.alert]).map(([t, alert], i) => `<span class="dd-box-sub${alert ? " dd-box-alert" : ""}">${i === 0 && wait ? `<span class="dd-needs" aria-hidden="true">${window.Status ? Status.icon("needs") : ""}</span>` : ""}${esc(t)}</span>`).join("");
    return `<button type="button" class="dd-box dd-box-${k}${wait ? " dd-wait" : ""}" data-box="${k}" aria-pressed="${pressed}"${off ? " disabled" : ""}>`
      + `<span class="dd-box-k"><span class="dd-box-title">${title}</span>${show}</span>`
      + `<span class="dd-box-v"><span class="dd-box-n">${esc(n)}</span><span class="dd-box-subs">${subs}</span></span></button>`;
  }
  // the record flow: imported → left → pairs to decide, with the removed duplicates branching off below
  function flowHtml(f, pressed) {
    const box = (k) => boxHtml(k, BOXES.find((b) => b[0] === k)[1], f, k === pressed);
    return box("imported") + '<span class="dd-arrow dd-arrow-1" aria-hidden="true"><i class="dd-branch"></i><i class="dd-branch-to"></i></span>'
      + box("left") + '<span class="dd-arrow dd-arrow-2" aria-hidden="true"></span>' + box("queue") + box("removed");
  }

  // "Duplicate (you)", "Unsure (ben)", "Duplicate (copied from raw records 3 and 7, ana)"; "Open" for an open pair
  function statusText(r, me) {
    if (!r.status || r.status === "open") return "Open";
    const who = r.signed_by ? (me && r.signed_by === me ? "you" : r.signed_by) : "signer not recorded";
    const src = r.source ? `copied from raw records ${r.source[0]} and ${r.source[1]}, ` : "";
    return `${STATUS_WORD[r.status] || r.status_words || r.status} (${src}${who})`;
  }
  const statusChip = (r, me) => chip(STATUS_KEY[r.status] || "open", statusText(r, me));
  const queueHeading = (f) => `${f.to_decide} open, ${f.decided} decided`;
  const youMark = '<span class="dd-you">You</span>';

  // a list row: number, title (two lines at most), then the sources and year at the left and the status at the right;
  // a pair's number is its place in the list as shown, the same count as "Pair N of M" above it (D32 F22)
  const rowHtml = (i, key, n, title, meta, right) => `<tr data-idx="${i}" data-key="${esc(key)}"><td class="num">${esc(n)}</td>
      <td class="dd-row"><div class="dd-row-t">${esc(title || "(no title)")}</div><div class="dd-row-m"><span class="dd-row-src">${meta}</span><span class="dd-status">${right}</span></div></td></tr>`;
  function rowsHtml(box, rows, me) {
    if (!rows.length) return `<tbody><tr><td class="muted">${box === "queue" ? "No pair to decide." : "Nothing to show."}</td></tr></tbody>`;
    const sub = (...xs) => xs.filter(Boolean).map(esc).join(" · ");
    // column names for screen readers only (WCAG 1.3.1); the list looks the same
    const head = `<thead class="sr-only"><tr><th>${box === "left" ? "Record" : "Number"}</th><th>Title, source and status</th></tr></thead>`;
    if (box === "queue") return head + "<tbody>" + rows.map((r, i) => rowHtml(i, keyOf(r), i + 1, r.title_a || r.title_b,
      sub(r.platform_a, r.platform_b !== r.platform_a ? r.platform_b : "", r.year_a || r.year_b), statusChip(r, me))).join("") + "</tbody>";
    if (box === "removed") return head + "<tbody>" + rows.map((r, i) => rowHtml(i, keyOf(r), i + 1, r.title_b || r.title_a,
      sub(r.platform_a, r.platform_b !== r.platform_a ? r.platform_b : "", r.year_b || r.year_a),
      (r.manual ? chip("dup", "Removed by your decision") : chip("wider", r.reason)) + (r.manual && me && r.by === me ? " " + youMark : ""))).join("") + "</tbody>";
    if (box === "left") return head + "<tbody>" + rows.map((r, i) => rowHtml(i, keyOf(r), r.record_id, r.title,
      sub(r.authors_short, r.year), `<span class="muted">from ${plural(r.n_raw || 0, "imported record")}</span>`)).join("") + "</tbody>";
    return "";
  }
  // the files deduplication reads (GET /api/import/sources, as `wider import list`), read only
  function uploadsHtml(d) {
    const fin = d.final_at ? `<p class="muted small">Uploads marked final on ${esc(when(d.final_at))}.</p>` : "";
    if (!d.sources.length) return `<tbody><tr><td class="muted">No files imported yet.</td></tr></tbody>`;
    return `<thead><tr><th>platform</th><th>file</th><th>records</th><th>imported at</th><th>query version</th></tr></thead><tbody>` + d.sources.map((r) => `<tr><td>${esc(r.platform)}</td><td>${esc(r.file)}${r.included === false ? ' <span class="muted">(file excluded: not used)</span>' : ""}</td><td>${esc(r.records)}</td><td>${esc(when(r.imported_at))}</td><td>${r.query_version == null ? "not recorded" : "v" + esc(r.query_version)}</td></tr>`).join("") + "</tbody>" + (fin ? `<caption class="dd-final">${fin}</caption>` : "");
  }

  function fieldRow(label, a, b, marked) {
    const w = matchWord(a, b), m = marked ? markDiff(a || "", b || "") : { a: esc(a || ""), b: esc(b || "") };
    return `<tr class="dd-f-${w}"><th scope="row">${label}</th><td>${m.a || missing}</td><td>${m.b || missing}</td>${matchCell(w)}</tr>`;
  }
  // the unique record's number beside each side, as a refusal names it ("records 12 and 40 now merge into one")
  const colHead = (word, platform, rid) => `${word}${rid != null ? ` <span class="dd-col-id">· record ${esc(rid)}</span>` : ""}${platform ? ` <span class="dd-col-src">· ${esc(platform)} export</span>` : ""}`;
  // an abstract longer than this is cut to four lines, with "Show full abstract"
  const FOLD = 260;
  function pairHtml(r, pos, me) {
    const f = (k) => [r[k + "_a"], r[k + "_b"]];
    const fields = [["Title", "title", true], ["Authors", "authors"], ["Year", "year"], ["Journal", "journal"], ["Volume", "volume"],
      ["Pages", "pages"], ["DOI", "doi"]].map(([label, k, marked]) => fieldRow(label, ...f(k), marked)).join("");
    const [aa, ab] = f("abstract"), aw = matchWord(aa, ab), am = markDiff(aa || "", ab || ""), long = String(aa || "").length > FOLD || String(ab || "").length > FOLD;
    const abs = `<tr class="dd-f-${aw} dd-absrow${long ? " folded" : ""}"><th scope="row">Abstract</th><td><div class="dd-clamp">${am.a || missing}</div></td><td><div class="dd-clamp">${am.b || missing}</div></td>${matchCell(aw)}</tr>`;
    // who signs (D33): the name WIDER works as, changed with Change in the top bar; no name field here
    const name = me ? `<p class="dd-name small">Deciding as <b>${esc(me)}</b></p>`
      : `<p class="dd-name small">No name yet: duplicate decisions are signed. <button type="button" class="secondary small" data-set-name>Set your name</button></p>`;
    return `<div class="dd-pair-head"><h2 class="dw-title" id="dw-title" tabindex="-1" aria-label="${esc(`${r.title_a || r.title_b || "(no title)"}, pair ${pos[0]} of ${pos[1]}`)}">Pair ${pos[0]} of ${pos[1]}</h2><span class="dd-matched">Matched on: ${esc(r.reason_words || r.stage || "")}</span>${statusChip(r, me)}</div>
      <table class="dd-fields"><thead><tr><th></th><th class="dd-col-a">${colHead("Record A", r.platform_a, r.record_a)}</th><th class="dd-col-b">${colHead("Record B", r.platform_b, r.record_b)}</th><th>Match</th></tr></thead><tbody>${fields}${abs}</tbody></table>
      ${long ? '<p class="dd-unfold-line"><button type="button" class="btn-quiet small-btn dd-ghost dd-link" data-unfold aria-expanded="false">Show full abstract</button></p>' : ""}
      <div class="dd-qrow"><p class="dd-question">Are these the same paper?</p><span class="dd-keys small muted">Keys while the focus is in the list or this pair: <kbd>1</kbd> duplicates, keep A · <kbd>2</kbd> keep B · <kbd>3</kbd> not duplicates · <kbd>4</kbd> unsure · <kbd>j</kbd> / <kbd>k</kbd> next and previous · <kbd>Esc</kbd> back to the list</span></div>${name}`;
  }
  function mergedHtml(r, pos, me) {
    const f = (k) => [r[k + "_a"], r[k + "_b"]];
    const fields = [["Title", "title", true], ["Authors", "authors"], ["Year", "year"], ["Journal", "journal"], ["DOI", "doi"]].map(([label, k, marked]) => fieldRow(label, ...f(k), marked)).join("");
    return `<div class="dd-pair-head"><h2 class="dw-title" id="dw-title" tabindex="-1" aria-label="${esc(`${r.title_b || r.title_a || "(no title)"}, removed pair ${pos[0]} of ${pos[1]}`)}">Removed pair ${pos[0]} of ${pos[1]}</h2> ${r.manual ? chip("dup", "Removed by your decision") : chip("wider", "by WIDER")}${r.manual && me && r.by === me ? " " + youMark : ""}</div>
      <p class="dd-removed">${esc(r.text)}</p>
      <table class="dd-fields"><thead><tr><th></th><th>${colHead("Kept", r.platform_a, r.record_a)}</th><th>${colHead("Removed", r.platform_b, r.record_b)}</th><th>Match</th></tr></thead><tbody>${fields}</tbody></table>
      <p class="muted small">Read only. ${r.manual ? "Change this decision through the notebook's undo or by deciding the pair again in Pairs to decide." : "A merge by WIDER cannot be undone here."}</p>`;
  }
  function recordHtml(r, pos) {
    return `<div class="dd-pair-head"><h2 class="dw-title" id="dw-title" tabindex="-1">${esc(r.title || "(no title)")}</h2></div>
      <p class="dw-meta">${[r.authors, r.year, r.journal].filter(Boolean).map(esc).join(" · ")}</p>
      <p class="small">Record ${esc(r.record_id)} (${pos[0]} of ${pos[1]}), made from ${plural(r.n_raw || 0, "imported record")}. Read only.</p>`;
  }
  // one notebook or pair-history entry: who (WIDER, You or the name), what, when, "(undone)"
  function whoHtml(who, me) { return who === "WIDER" ? chip("wider", "WIDER") : (me && who === me ? youMark : `<span class="dd-who">${esc(who || "signer not recorded")}</span>`); }
  function historyHtml(entries, me) {
    if (!entries.length) return '<li class="muted small">Nothing recorded for this pair yet.</li>';
    return entries.map((e) => `<li><time class="muted">${esc(when(e.at))}</time><span class="dd-hist-who">${whoHtml(e.who, me)}</span><span>${esc(e.text)}${e.undone ? " <i>(undone)</i>" : ""}</span></li>`).join("");
  }
  function notebookHtml(entries, me, shown) {
    const items = entries.map((e, i) => {
      let act = "";
      if (e.kind === "decision" && !e.undone && e.change_id != null) act = `<button type="button" class="btn-quiet small-btn" data-undo="${esc(e.change_id)}">Undo</button>`;
      else if (e.kind === "undo" && !e.undone && e.change_id != null) act = `<button type="button" class="btn-quiet small-btn" data-undo="${esc(e.change_id)}">Redo</button>`;
      else if (e.kind === "run" && e.result) act = shown === i ? "" : `<button type="button" class="btn-quiet small-btn" data-show="${i}">Show this result</button>`;
      const card = shown === i && e.result ? `<div class="dd-nb-result">${window.resultCardHtml ? resultCardHtml(e.result, { chosen: true }) : esc(e.result.headline)}</div>` : "";
      return `<li class="dd-nb-entry">${whoHtml(e.who, me)} <span>${esc(e.text)}${e.undone ? " <i>(undone)</i>" : ""}</span>
        <div class="muted small">${esc(when(e.at))} ${act}</div>${card}</li>`;
    }).join("");
    return `<div class="dd-panel-head"><h2>Step notebook</h2><button type="button" class="icon-btn" data-close aria-label="Close the notebook">&#215;</button></div>
      <p id="dd-nb-msg" class="small" role="status"></p>${entries.length ? `<ol class="dd-nb">${items}</ol>` : '<p class="muted">Nothing recorded for this step yet.</p>'}`;
  }
  // the lead is the first sentence of "Why it matters" (the six answers, ui-redesign D6), else of the first part
  const leadOf = (g) => { const part = g && g.parts && (g.parts.find((p) => p.heading === "Why it matters") || g.parts[0]);
    const t = (part && part.items[0]) || ""; const m = t.match(/^.*?[.!?](\s|$)/); return (m ? m[0] : t).trim(); };

  // ---------------------------------------------------------------- the page's state and its drawing
  const S = { d: null, box: null, sel: null, limit: 50, rows: [], view: [], lists: {}, notebook: [], guide: null,
              panel: null, shown: null, fresh: true };
  const q = (s) => document.querySelector(s);
  const me = () => (window.Drawer ? Drawer.author() : "");
  const flow = () => (S.d && S.d.flow) || {};

  function drawHead() {
    const f = flow();
    q("#dd-chip").innerHTML = chipHtml(f);
    q("#dd-flow").innerHTML = flowHtml(f, S.box);
    q("#dd-msg").textContent = f.message || "";
    // pairs waiting: the message is drawn in rose with the needs-you mark before it
    const wait = !!(f.has_run && f.to_decide > 0), ic = q("#dd-msg-icon");
    ic.hidden = !wait; ic.innerHTML = wait && window.Status ? Status.icon("needs") : "";
    q("#dd-msg").classList.toggle("dd-msg-wait", wait);
    q("#dd-hint").hidden = !f.has_run;
    q("#dd-continue").hidden = !f.has_run;
    if (S.box === "queue") setHeading("Pairs to decide", queueHeading(f), f.to_decide);
    fitWork();
  }
  // the list and the pair take the rest of the window below the chart (the CSS uses it only on a wide, tall window)
  function fitWork() {
    const w = q(".dd-work");
    const root = document.documentElement;
    if (!w || !root || !root.style || !w.getClientRects || !w.getClientRects().length) return;   // hidden: keep the last value
    root.style.setProperty("--dd-work-top", Math.round(w.getBoundingClientRect().top + (window.scrollY || 0)) + "px");
  }
  if (typeof window.addEventListener === "function") window.addEventListener("resize", () => fitWork());
  function pressBox(k) {
    if (!k || S.box === k) return;
    S.box = k; S.sel = null; S.limit = 50;
    drawHead(); drawList();
    const b = q("#dd-flow").querySelector(`[data-box="${k}"]`); if (b && b.focus) b.focus();   // the redraw replaced the box
  }
  function setHeading(kind, count, n) {
    q("#dd-list-head").innerHTML = `<span class="dd-list-kind">${esc(kind)}</span>${n == null ? "" : ` <span class="dd-list-n">${esc(n)}</span>`} <span class="dd-list-sub">Shown by the chart · <span id="dd-list-count" class="dd-list-count">${esc(count)}</span></span>`;
  }
  function drawDetails() {
    const res = (typeof LAST_RESULTS !== "undefined" && LAST_RESULTS.dedup) || ((S.notebook.find((e) => e.kind === "run" && e.result) || {}).result);
    q("#dd-result").innerHTML = res && window.resultCardHtml ? resultCardHtml(res) : "";
    q("#dd-runtable").innerHTML = Object.entries((S.d && S.d.summary) || {}).map(([k, v]) => `<tr><th scope="row" class="kv">${esc(k.replace(/_/g, " "))}</th><td>${esc(v)}</td></tr>`).join("");
  }
  function drawTopbar() {
    if (!window.Topbar) return;
    Topbar.set("dedup", {
      notebook: { label: "Notebook", count: () => S.notebook.length, expanded: () => S.panel === "notebook", onclick: () => togglePanel("notebook") },
      main: { label: () => (flow().has_run ? "Apply decisions and check again" : "Run deduplication"), onclick: run },
    });
  }

  // the list of the pressed box; queue rows come with /api/dedup, the others are fetched as the box is pressed
  async function fetchList(box) {
    if (box === "queue") return;
    const need = box === "imported" ? "/api/import/sources" : `/api/dedup/${box === "removed" ? "merged" : "records"}?limit=${S.limit}`;
    const have = S.lists[box];
    if (have && (box === "imported" || have.rows.length >= Math.min(S.limit, have.total))) return;
    S.lists[box] = await api(need);
  }
  function viewRows() {
    const terms = words(q("#dd-search").value);
    const hit = (t) => { const w = new Set(words(t)); return terms.every((x) => w.has(x)); };
    if (S.box === "queue") {
      let rows = S.rows.filter((r) => hit(`${r.title_a || ""} ${r.title_b || ""} ${r.authors_a || ""} ${r.authors_b || ""}`));
      if (q("#dd-openfirst").checked) rows = rows.filter((r) => r.status === "open").concat(rows.filter((r) => r.status !== "open"));
      return rows;
    }
    const l = S.lists[S.box];
    if (!l) return [];
    if (S.box === "removed") return l.rows.filter((r) => hit(`${r.title_a || ""} ${r.title_b || ""} ${r.authors_a || ""} ${r.authors_b || ""}`));
    if (S.box === "left") return l.rows.filter((r) => hit(`${r.title || ""} ${r.authors_short || ""}`));
    return l.sources || [];
  }
  async function drawList() {
    const box = S.box, t = q("#dd-list");
    q("#dd-error").hidden = true;
    try { await fetchList(box); } catch (e) { t.innerHTML = `<tbody><tr><td class="bad">${esc(e.message)}</td></tr></tbody>`; return; }
    if (box !== S.box) return;   // another box was pressed meanwhile
    const l = S.lists[box], f = flow();
    q("#dd-approx").hidden = !(box === "removed" && l && l.approximate);
    q("#dd-approx").textContent = "(approximate: this run was made before WIDER kept its list; run deduplication again for an exact list)";
    q("#dd-search").closest(".dd-tools").hidden = box === "imported";
    q("#dd-openfirst").closest("label").hidden = box !== "queue";
    if (box === "queue") setHeading("Pairs to decide", queueHeading(f), f.to_decide);
    else if (box === "removed") setHeading("Duplicates removed", `${plural(l.total, "merged pair")}, one per removed record`, l.total);
    else if (box === "left") setHeading("Records left", plural(l.total, "record"), l.total);
    else setHeading("Records imported", `${plural((l.sources || []).filter((r) => r.included !== false).length, "file")} that deduplication reads`);
    if (box === "imported") {
      t.innerHTML = uploadsHtml(l); q("#dd-more").hidden = true;
      if (window.Drawer && Drawer.isDocked()) Drawer.close(true);
      pairEmpty("The uploads are listed for reading. Add or change them on Search results.");
      return;
    }
    S.view = viewRows();
    const shown = S.view.slice(0, S.limit), total = box === "queue" ? S.view.length : Math.max(S.view.length, l.total || 0);
    t.innerHTML = rowsHtml(box, shown, me());
    const more = q("#dd-more"); more.hidden = shown.length >= total;
    q("#dd-shown").textContent = total ? `${shown.length} of ${total} shown` : "";
    more.textContent = `Show ${Math.min(50, total - shown.length)} more`;
    more.onclick = () => { S.limit += 50; drawList(); };
    if (window.Drawer) Drawer.bindRows(t, (i) => openItem(shown, i, true));
    if (!shown.length) { if (window.Drawer && Drawer.isDocked()) Drawer.close(true); pairEmpty(box === "queue" ? "No pair to decide." : "Nothing to show."); return; }
    const at = Math.max(0, shown.findIndex((r) => keyOf(r) === S.sel));
    openItem(shown, at, false);
  }
  function pairEmpty(text) {
    let p = q("#dd-pair-empty");
    const box = q("#dd-pair");
    if (!p) { box.insertAdjacentHTML("afterbegin", '<p class="muted dd-pair-empty" id="dd-pair-empty"></p>'); p = q("#dd-pair-empty"); }
    p.hidden = !text; p.textContent = text || "";
    const hist = q("#dd-hist"); if (hist && text) hist.hidden = true;
  }

  // the docked drawer on one item of the shown list; `focus` when a row was opened, not when the page drew itself
  function openItem(items, i, focus) {
    if (!window.Drawer) return;
    const box = S.box;
    pairEmpty("");
    S.sel = keyOf(items[i]);
    // "Pair 2 of 12": counted over the whole list, not only the rows shown so far
    const total = box === "queue" ? S.view.length : Math.max(S.view.length, (S.lists[box] || {}).total || 0);
    const pos = (r) => [items.indexOf(r) + 1, Math.max(total, items.length)];
    let quiet = !focus;
    const base = { dock: "#dd-pair", table: "#dd-list", items, index: i, notes: false, focus,
      onEsc: () => { const tr = q("#dd-list").querySelector("tr.active") || q("#dd-list").querySelector("tr[data-idx]"); if (tr) tr.focus(); },
      onMove: (idx, r) => {
        S.sel = keyOf(r);
        // the page drawing itself marks the row without scrolling the window to it; j/k and clicks bring it into view
        if (quiet) { quiet = false; q("#dd-list").querySelectorAll("tr[data-idx]").forEach((tr) => tr.classList.toggle("active", +tr.dataset.idx === idx)); }
        else Drawer.markRow(q("#dd-list"), idx);
      } };
    if (box === "queue") Drawer.open({ ...base, label: "Pair to decide",
      render: async (r) => pairHtml(r, pos(r), me()),
      afterRender: (r, body) => afterPair(r, body),
      current: (r) => currentDecision(r),
      // owner walkthrough P8: keep A pink, keep B purple, Not duplicates green, Unsure yellow
      decisions: [
        { value: "keep_a", label: "Duplicates, keep A", tone: "a" },
        { value: "keep_b", label: "Duplicates, keep B", tone: "b" },
        { value: "not_duplicate", label: "Not duplicates", tone: "not" },
        { value: "uncertain", label: "Unsure", tone: "unsure" },
      ].map((d) => ({ ...d, fn: (r) => decide(r, d.value, d.label) })) });
    else if (box === "removed") Drawer.open({ ...base, label: "Removed pair", render: async (r) => mergedHtml(r, pos(r), me()), afterRender: () => histHide() });
    else Drawer.open({ ...base, label: "Record left", render: async (r) => recordHtml(r, pos(r)), afterRender: () => histHide() });
  }
  function histBox() {
    let hb = q("#dd-hist");
    if (!hb) { q("#dd-pair").insertAdjacentHTML("beforeend", `<section class="dd-hist" id="dd-hist" aria-label="This pair's history"><div class="dd-hist-head"><h3>This pair's history</h3><a href="#dedup" id="dd-hist-full" class="small">Full step notebook</a></div><ul id="dd-hist-list"></ul></section>`); hb = q("#dd-hist"); }
    q("#dd-pair").appendChild(hb);   // under the docked drawer
    q("#dd-hist-full").onclick = (e) => { e.preventDefault(); openPanel("notebook"); };
    return hb;
  }
  function histHide() { const hb = q("#dd-hist"); if (hb) hb.hidden = true; }
  async function afterPair(r, body) {
    const uf = body.querySelector("[data-unfold]");
    if (uf) uf.onclick = () => { const c = body.querySelector(".dd-absrow"), open = c.classList.toggle("folded") === false; uf.textContent = open ? "Show less" : "Show full abstract"; uf.setAttribute("aria-expanded", String(open)); };
    const hb = histBox(); hb.hidden = false;
    const ul = q("#dd-hist-list"); ul.innerHTML = '<li class="muted small">loading…</li>';
    try {
      const entries = await api(`/api/notebook/dedup?a=${encodeURIComponent(r.raw_a)}&b=${encodeURIComponent(r.raw_b)}`);
      if (Drawer.current() === r) ul.innerHTML = historyHtml(entries, me());
    } catch (e) { ul.innerHTML = `<li class="bad small">${esc(e.message)}</li>`; }
  }

  // which of the four buttons a decided pair shows pressed: a duplicate with no kept record (decided before P8, or by
  // the CLI without --keep) presses none of the two keep buttons
  function currentDecision(r) {
    const k = r.diagnostics && r.diagnostics.keep;
    if (r.status === "manual_same") return k === r.raw_a ? "keep_a" : k === r.raw_b ? "keep_b" : null;
    return { manual_different: "not_duplicate", manual_uncertain: "uncertain" }[r.status] || null;
  }

  async function decide(r, v, label) {
    // no browser prompt (some browsers block it): without a name nothing is sent and the pair view points at Change
    const by = me();
    if (!by) throw Object.assign(new Error(WiderName.NEED), { needName: true });
    const keep = v === "keep_a" ? r.raw_a : v === "keep_b" ? r.raw_b : null;
    const decision = keep === null ? v : "duplicate";
    const saved = await api("/api/dedup/decide", { method: "POST", body: JSON.stringify({ raw_a: r.raw_a, raw_b: r.raw_b, decision, by, keep }) });
    if (window.offerUndo) offerUndo(saved.change_id, `Pair marked ${label.charAt(0).toLowerCase() + label.slice(1)}.`);
    r.status = DECIDED_AS[decision]; r.signed_by = by; r.source = null;
    r.diagnostics = { ...(r.diagnostics || {}), keep };
    const tr = q("#dd-list").querySelector(`tr[data-key="${keyOf(r)}"] .dd-status`);
    if (tr) tr.innerHTML = statusChip(r, by);
    refreshCounts();   // the chart, message, heading and Notebook count follow; the list and the pair view stay
    if (window.refreshStages) refreshStages();
  }
  async function refreshCounts() {
    try {
      const [d, nb] = await Promise.all([api("/api/dedup"), api("/api/notebook/dedup")]);
      S.d = d; S.notebook = nb;
      drawHead(); drawDetails(); drawTopbar();
      if (S.panel === "notebook") drawPanel();
    } catch (e) { /* the next load shows it */ }
  }

  // ---------------------------------------------------------------- the side panel (the notebook; the guide is the shared panel)
  function togglePanel(p) {
    S.panel = S.panel === p ? null : p; S.shown = null;
    drawPanel(); if (window.Topbar) Topbar.redraw();
    if (S.panel) return focusPanel();   // opened: the focus goes into the panel, so the keyboard need not cross the list
    const b = q(`#tb-${p}-btn`); if (b) b.focus();   // closed: the redraw replaced the button, the focus goes back to it
  }
  function focusPanel() { const pn = S.panel && q(`#dd-${S.panel}`); if (pn) pn.focus(); }
  // Esc inside a panel closes it (and is not passed on to the docked pair view, which would move to the list)
  function panelKey(e) {
    if (e.key !== "Escape" || !S.panel) return;
    e.preventDefault(); e.stopPropagation(); togglePanel(S.panel);
  }
  function hashLeft() { if (location.hash.replace("#", "") !== "dedup") S.fresh = true; }
  function openPanel(p) { if (S.panel !== p) togglePanel(p); }
  function drawPanel() {
    const nb = q("#dd-notebook");
    nb.hidden = S.panel !== "notebook";
    if (S.panel === "notebook") {
      nb.innerHTML = notebookHtml(S.notebook, me(), S.shown);
    } else nb.innerHTML = "";
  }
  // the panels' buttons, one listener each (their contents are redrawn)
  async function notebookClick(t) {
    if (t.closest("[data-close]")) return togglePanel("notebook");
    // the panel is redrawn under the pressed button, so the focus stays in the panel rather than falling to the page
    const show = t.closest("[data-show]"); if (show) { S.shown = +show.dataset.show; drawPanel(); return focusPanel(); }
    if (t.closest("[data-latest]")) { S.shown = null; drawPanel(); return focusPanel(); }
    const b = t.closest("[data-undo]"); if (!b) return;
    b.disabled = true;
    try { await api(`/api/history/${encodeURIComponent(b.dataset.undo)}/undo`, { method: "POST" }); if (window.Undo) await Undo.refreshAll(); else await load(); focusPanel(); }
    catch (e) { q("#dd-nb-msg").textContent = e.message; b.disabled = false; }   // the journal's refusal, in words
  }

  // ---------------------------------------------------------------- the main button
  async function run() {
    const err = q("#dd-error"), btn = q("#tb-main-btn");
    err.hidden = true; err.textContent = "";
    q("#dd-msg").textContent = "Running deduplication…";
    if (btn) btn.disabled = true;
    try {
      const r = await api("/api/dedup/run", { method: "POST" });
      if (window.showStageResult) showStageResult(r.stage_result);
      S.lists = {};
      await load();
      // a merge of two screened records: which record's screening was kept, said once beside the chart (also in History)
      if (r.set_aside && r.set_aside.length) q("#dd-msg").textContent = r.set_aside.join(" ");
      if (window.refreshStages) refreshStages();
    } catch (e) {
      err.textContent = e.message; err.hidden = false;   // beside the chart: a refused re-run (409), a 500 or no network
      q("#dd-msg").textContent = flow().message || "";
    } finally { const b = q("#tb-main-btn"); if (b) b.disabled = false; }
  }

  // ---------------------------------------------------------------- load: on opening the page and after every action
  async function load() {
    const first = S.fresh;
    if (S.fresh) { S.fresh = false; S.panel = null; S.box = null; S.sel = null; S.limit = 50; }
    // the page's lead comes from the guide; the shared guide panel is asked again, its notices count the pairs still waiting
    const [d, nb, g] = await Promise.all([api("/api/dedup"), api("/api/notebook/dedup").catch(() => []), api("/api/guide/dedup").catch(() => S.guide)]);
    S.d = d; S.notebook = nb; S.guide = g; S.lists = {};
    S.rows = d.queue || [];
    if (!S.box || (!d.flow.has_run && S.box !== "imported")) S.box = defaultBox(d.flow);
    q("#dd-lead").textContent = leadOf(g);
    drawHead(); drawDetails(); drawTopbar(); drawPanel();
    if (!first && window.Guide && Guide.reload) Guide.reload();   // the first draw: the router's own visit has asked already
    await drawList();
    if (window.SettingsCard) await SettingsCard.draw("#dd-settings", "dedup");
  }

  if (typeof document !== "undefined" && document.querySelector && document.querySelector("#dd-search")) {
    q("#dd-search").oninput = () => drawList();
    q("#dd-flow").onclick = (e) => { const b = e.target.closest("[data-box]"); if (b && !b.disabled) pressBox(b.dataset.box); };
    q("#dd-notebook").onclick = (e) => notebookClick(e.target);
    q("#dd-notebook").onkeydown = panelKey;
    q("#dd-openfirst").onchange = () => drawList();
    // a page opened anew starts closed and on its default box; marked on leaving, because the router's own hashchange
    // listener (app.js, registered first) has already called load() when this one runs
    window.addEventListener("hashchange", hashLeft);
  }

  window.DedupView = Object.assign(window.DedupView || {}, { matchWord, markDiff, load, chipHtml, flowHtml, defaultBox, statusText,
    queueHeading, rowsHtml, uploadsHtml, pairHtml, mergedHtml, historyHtml, notebookHtml, leadOf, state: S, pressBox, togglePanel, panelKey, hashLeft, run, notebookClick });
})();
