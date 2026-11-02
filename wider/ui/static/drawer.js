// Record drawer (WP13): one right-hand panel for record work on every page (labelling, adjudication, dedup pairs,
// screening review, development evaluation, notes search). Keys: 1/2/3 decide, j/k next/previous, n note, t tag,
// Esc close. Pages call Drawer.open({...}); see records.js for the page integrations. With cfg.dock (a selector) the
// drawer sits in that container beside the page's list instead of over the page: no close button, Esc calls
// cfg.onEsc (the page focuses its list), and the keys work only while that page is shown (design-pass-a1-dedup D8).
// Single keys (WCAG 2.1.4, owner fixes 1 Nov 2026) act only while the focus is in the drawer, or for a docked drawer
// in its box or its page's list (cfg.table); never from a field, a toast or anywhere else on the page.
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const h = esc;
  const call = api;

  let cfg = null, idx = 0, lastFocus = null, token = 0;
  let loading = false, pendingFocus = null, shown = null;   // shown: the item last drawn  // n or t pressed while the next record loads waits for it
  // a decision being saved, or the short pause before the drawer moves on: a second key press then decides nothing
  let deciding = false, advanceTimer = null, refusedWhileSaving = false;
  let ignored = 0;   // key presses refused (saving, pause, loading): said on the next record too, never dropped silently
  let lastSaved = null;   // {item, i, same}: the same decision pressed again on the same record sends nothing more
  let dockedOn = null;   // the page (location.hash) whose container holds the docked drawer
  const el = q("#drawer");
  const home = el.parentNode;   // where the overlay drawer lives; a docked drawer goes back there on close

  // ---------------------------------------------------------------- quotes highlighted in the abstract
  // The model's quotes (fragments split at "...", at least 8 characters, whitespace-insensitive, case-insensitive)
  // are marked where they occur; the rest of the text is escaped. Mirrors wider.core.matching.quote_verified.
  function highlight(text, quotes) {
    const src = String(text || "");
    const ranges = [];
    (quotes || []).forEach(qt => String(qt || "").replace(/…/g, "...").split("...").map(f => f.trim()).filter(f => f.length >= 8).forEach(f => {
      const pat = f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
      let re; try { re = new RegExp(pat, "gi"); } catch (e) { return; }
      let m; while ((m = re.exec(src)) !== null) { ranges.push([m.index, m.index + m[0].length]); if (!m[0].length) break; }
    }));
    ranges.sort((a, b) => a[0] - b[0]);
    let out = "", pos = 0;
    for (const [a, b] of ranges) {
      if (b <= pos) continue;
      const s = Math.max(a, pos);
      out += h(src.slice(pos, s)) + "<mark>" + h(src.slice(s, b)) + "</mark>";
      pos = b;
    }
    return out + h(src.slice(pos));
  }

  // after a save re-renders the notes, put focus back in the field only if the reviewer has not moved on meanwhile
  // (Esc, n or t pressed while the request ran); otherwise the late re-render would steal their focus
  function refocus(sel) {
    const a = document.activeElement;
    if (!a || a === document.body || !a.isConnected) { const el = q(sel); if (el) el.focus(); }
  }

  function author() {
    if (cfg && cfg.author) return cfg.author;   // a queue loaded for one name signs its notes and tags with that name
    return WiderName.get();   // otherwise the one name WIDER works as (D33); the drawer has no name field
  }

  // ---------------------------------------------------------------- record header (title, venue, year, abstract)
  function recordBlock(rec, quotes) {
    const meta = [rec.authors, rec.year, rec.journal].filter(Boolean).map(h).join(" · ");
    const doi = rec.doi ? ` · <a href="https://doi.org/${h(rec.doi)}" target="_blank" rel="noopener noreferrer">${h(rec.doi)}</a>` : "";
    return `<h2 class="dw-title" id="dw-title" tabindex="-1">${h(rec.title || "(no title)")}</h2>
      <p class="dw-meta">${meta}${doi}</p>
      <div class="dw-abstract">${rec.abstract ? highlight(rec.abstract, quotes) : '<span class="muted">(no abstract)</span>'}</div>${versionsBlock(rec.abstract_versions)}`;
  }
  // every abstract the record has had (reviewer-roles-and-blinding D8; `wider enrich --versions`)
  function versionsBlock(vs) {
    if (!vs || !vs.length) return "";
    const last = vs[vs.length - 1].version;
    return `<details class="dw-versions"><summary>Abstract versions: ${vs.length}</summary><ul>${vs.map(v =>
      `<li>${h(v.version)}  ${h(v.source_word)}, ${h(v.words || 0)} words, ${v.at ? h(fmtWhen(v.at)) : "date not recorded"}${v.version === last ? "  (current)" : ""}</li>`).join("")}</ul></details>`;
  }

  // ---------------------------------------------------------------- notes and tags
  async function renderNotes(recordId) {
    const box = q("#dw-notes");
    if (!recordId || cfg.notes === false) {
      box.hidden = !cfg.historyNote;
      // an item without a record id (re-check and second-reviewer queues) says where its history would be
      box.innerHTML = cfg.historyNote ? `<p class="dw-history-note">${h(cfg.historyNote)}</p>` : "";
      box.dataset.rec = "";
      return;
    }
    box.hidden = false;
    const me = author();
    let d;
    // blind mode without a name shows nobody's notes (the server refuses a blind request without one)
    try { d = cfg.blind && !me ? { notes: [], tags: [] } : await call(`/api/records/${recordId}?author=${encodeURIComponent(me)}&blind=${cfg.blind ? "true" : "false"}`); }
    catch (e) { box.innerHTML = `<p class="bad small">${h(e.message)}</p>`; return; }
    const mine = Array.from(me || "").slice(0, 80).join("");  // the server keeps the first 80 characters of a name
    const tags = d.tags.map(t => `<span class="chip tag-chip" title="${h(t.author)}">${h(t.tag)}${t.author === mine ? ` <button class="chip-x" data-untag="${h(t.tag)}" aria-label="Remove tag ${h(t.tag)}">×</button>` : ""}</span>`).join(" ");
    const notes = d.notes.map(n => `<li><span class="muted small">${h(n.author)} · ${h(fmtWhen(n.created_at))}</span>
      ${n.author === mine ? `<button class="chip-x" data-delnote="${n.note_id}" aria-label="Delete this note">×</button>` : ""}<div>${h(n.text)}</div></li>`).join("");
    // a re-render of the same record (after a save) keeps what is typed and the last message, read after the fetch,
    // so a refusal that arrives while an earlier save re-renders is still shown
    const val = (s) => (q(s) ? q(s).value : "");
    const kept = box.dataset.rec === String(recordId) ? { tag: val("#dw-tag"), note: val("#dw-note"), msg: q("#dw-notes-msg") ? q("#dw-notes-msg").innerHTML : "" } : null;
    box.innerHTML = `<h3>Notes and tags${cfg.blind ? ' <span class="muted small">(yours only, blind mode)</span>' : ""}</h3>
      <div class="dw-tags">${tags || '<span class="muted small">no tags</span>'}</div>
      <input id="dw-tag" placeholder="add a tag and press Enter (t)" aria-label="Add a tag" autocomplete="off">
      <ul class="dw-notelist">${notes}</ul>
      <textarea id="dw-note" rows="2" placeholder="add a note (n); Enter saves, Shift+Enter starts a new line" aria-label="Add a note"></textarea>
      <p class="small" id="dw-notes-msg" role="status"></p>
      <details class="dw-history" id="dw-history"><summary>History of this record${cfg.blind ? " (your changes)" : ""}</summary><ul></ul></details>`;
    box.dataset.rec = String(recordId);
    if (kept) { q("#dw-tag").value = kept.tag; q("#dw-note").value = kept.note; q("#dw-notes-msg").innerHTML = kept.msg; }
    q("#dw-history").ontoggle = async (ev) => {
      if (!ev.target.open) return;
      const ul = q("#dw-history ul");
      try {
        const rows = await call(`/api/history?record_id=${recordId}${cfg.blind ? `&blind=true&author=${encodeURIComponent(me)}` : ""}`);
        ul.innerHTML = rows.length ? rows.map(c => `<li><span class="muted">${h(fmtWhen(c.at))} · ${h(c.author || "")}${c.author_inferred ? " (inferred, not confirmed)" : ""}</span> ${h(c.summary)}${c.undone_by ? " <i>(undone)</i>" : ""}</li>`).join("") : "<li class='muted'>no changes yet</li>";
      } catch (err) { ul.innerHTML = `<li class="bad">${h(err.message)}</li>`; }
    };
    const msg = q("#dw-notes-msg");
    const say = (t) => { const m = q("#dw-notes-msg"); if (m) m.innerHTML = t; };   // the current box, after any re-render
    // without a name nothing is sent: the line points at Change in the top bar, its button opens the name dialog (D33)
    const needName = () => { const a = author(); if (!a) { msg.innerHTML = `<span class="bad">${WiderName.needHtml("")}</span>`; return null; } return a; };
    const changed = () => { if (cfg.onAnnotate) cfg.onAnnotate(recordId); };
    q("#dw-tag").onkeydown = async (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const a = needName(), tag = e.target.value; if (!a || !tag.trim()) return;
      e.target.value = ""; say("");   // sent once; a refusal gives the text back
      try { const r = await call(`/api/records/${recordId}/tags`, { method: "POST", body: JSON.stringify({ author: a, tag }) }); await renderNotes(recordId); refocus("#dw-tag"); changed(); if (window.offerUndo) offerUndo(r.change_id, `Tagged ${r.tag}.`); }
      catch (err) { const f = q("#dw-tag") || e.target; if (!f.value) f.value = tag; say(`<span class="bad">${h(err.message)}</span>`); }
    };
    q("#dw-note").onkeydown = async (e) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      e.preventDefault();
      const a = needName(), text = e.target.value; if (!a || !text.trim()) return;
      e.target.value = ""; say("");   // sent once: a second Enter while it saves finds the field empty
      try { const r = await call(`/api/records/${recordId}/notes${cfg.blind ? "?blind=true" : ""}`, { method: "POST", body: JSON.stringify({ author: a, text }) }); await renderNotes(recordId); refocus("#dw-note"); changed(); if (window.offerUndo) offerUndo(r.change_id, "Note added."); }
      catch (err) { const f = q("#dw-note") || e.target; if (!f.value) f.value = text; say(`<span class="bad">${h(err.message)}</span>`); }
    };
    box.querySelectorAll("[data-untag]").forEach(b => b.onclick = async () => {
      try { const r = await call(`/api/records/${recordId}/tags/${encodeURIComponent(b.dataset.untag)}?author=${encodeURIComponent(me)}`, { method: "DELETE" }); await renderNotes(recordId); changed(); if (window.offerUndo) offerUndo(r.change_id, "Tag removed."); }
      catch (err) { msg.textContent = err.message; }
    });
    box.querySelectorAll("[data-delnote]").forEach(b => b.onclick = async () => {
      try { const r = await call(`/api/records/${recordId}/notes/${b.dataset.delnote}?author=${encodeURIComponent(me)}`, { method: "DELETE" }); await renderNotes(recordId); changed(); if (window.offerUndo) offerUndo(r.change_id, "Note deleted."); }
      catch (err) { msg.textContent = err.message; }
    });
  }

  // ---------------------------------------------------------------- render one item
  async function render() {
    const my = ++token;
    loading = true;
    const item = cfg.items[idx];
    q("#dw-pos").textContent = `${idx + 1} / ${cfg.items.length}`;
    q("#dw-kind").textContent = cfg.label || "";
    q("#dw-prev").disabled = idx <= 0;
    q("#dw-next").disabled = idx >= cfg.items.length - 1;
    const body = q("#dw-body");
    body.innerHTML = '<p class="muted small">loading…</p>';
    if (item !== shown) {   // another record: nothing of the earlier one stays readable or clickable while this loads
      const box = q("#dw-notes"); box.innerHTML = ""; box.dataset.rec = "";
      if (cfg.decisions) q("#dw-decide").innerHTML = '<span class="dw-dec-msg" id="dw-dec-msg" role="status"></span>';
      shown = item; lastSaved = null;
    }
    let html;
    try { html = await cfg.render(item, { recordBlock, highlight, call, h }); }
    catch (e) { html = `<p class="bad">${h(e.message)}</p>`; }
    if (my !== token) return;   // a faster j/k already moved on
    body.innerHTML = html;
    if (cfg.afterRender) cfg.afterRender(item, body);
    const dec = q("#dw-decide");
    if (cfg.decisions) {
      const current = cfg.current ? cfg.current(item) : null;
      dec.hidden = false;
      // a decision with a tone is a big white button with a coloured left edge (.dbtn), its key first, pressed when chosen
      dec.innerHTML = cfg.decisions.map((d, i) => `<button ${d.tone ? `type="button" class="dbtn" data-tone="${h(d.tone)}" aria-pressed="${current === d.value}"` : `class="${current === d.value ? "" : "secondary"}"`} data-dec="${i}" aria-keyshortcuts="${i + 1}"${cfg.readOnly && cfg.readOnly(item) ? " disabled" : ""}>${d.tone ? `<kbd>${i + 1}</kbd> ${h(d.label)}` : `${h(d.label)} <kbd>${i + 1}</kbd>`}</button>`).join("") +
        `<span class="dw-dec-msg${ignored ? " dw-warn" : ""}" id="dw-dec-msg" role="status">${cfg.readOnly && cfg.readOnly(item) ? h(cfg.readOnlyText || "") : ignored ? h(ignoredText()) : ""}</span>`;
      ignored = 0;
      dec.querySelectorAll("[data-dec]").forEach(b => b.onclick = () => decide(+b.dataset.dec));
    } else { dec.hidden = true; dec.innerHTML = ""; }
    if (cfg.onMove) cfg.onMove(idx, item);
    await renderNotes(cfg.recordId ? cfg.recordId(item) : null);
    if (my === token) loading = false;
  }

  // after a render: a field asked for while loading (n, t) gets the focus, else the title when focus left the drawer
  function settleFocus(always) {
    if (!cfg) return;
    const want = pendingFocus && q(pendingFocus); pendingFocus = null;
    if (want) { want.focus(); return; }
    const a = document.activeElement;
    if (always || !a || a === el || !el.contains(a) || a.disabled) { const t = q("#dw-title"); (t || el).focus({ preventScroll: true }); }
  }

  async function decide(i) {
    if (!cfg || !cfg.decisions || !cfg.decisions[i]) return;
    const item = cfg.items[idx];
    if (cfg.readOnly && cfg.readOnly(item)) return;
    const msg = q("#dw-dec-msg");
    // a refused press is said in dark red, with how many were refused, so a fast run of keys is never lost silently
    const warn = (t) => { ignored++; if (msg) { msg.textContent = `${t} (${ignored} key press${ignored > 1 ? "es" : ""} ignored)`; msg.className = "dw-dec-msg dw-warn"; } };
    // a record still on its way has not been read yet, so nothing is decided for it
    if (loading) { warn("the next record is still loading: nothing was decided"); return; }
    const already = "already saved, moving to the next record: nothing more was decided";
    if (deciding || advanceTimer) { if (deciding) refusedWhileSaving = true; warn(already); return; }
    // the same decision again on the record it was saved for (the last record, or a list that does not move on)
    const same = cfg.sameAs ? String(cfg.sameAs(item, q("#dw-body"))) : "";
    if (lastSaved && lastSaved.item === item && lastSaved.i === i && lastSaved.same === same) {
      if (msg) { msg.textContent = `already saved as ${cfg.decisions[i].label.toLowerCase()}: nothing more was sent`; msg.className = "dw-dec-msg dw-warn"; }
      return;
    }
    deciding = true; refusedWhileSaving = false;
    try {
      await cfg.decisions[i].fn(item, q("#dw-body"));
      if (!cfg || cfg.items[idx] !== item) return;   // j/k while it was saving: the drawer has moved on, not again
      lastSaved = { item, i, same };
      // a second key pressed while this one saved keeps its answer: the save does not write over it
      if (msg && !refusedWhileSaving) { msg.textContent = `saved: ${cfg.decisions[i].label.toLowerCase()}`; msg.className = "dw-dec-msg"; }
      q("#dw-decide").querySelectorAll("[data-dec]").forEach(b => { if (b.dataset.tone) b.setAttribute("aria-pressed", String(+b.dataset.dec === i)); else b.className = +b.dataset.dec === i ? "" : "secondary"; });
      if (cfg && cfg.advance !== false && idx < cfg.items.length - 1) advanceTimer = setTimeout(() => { advanceTimer = null; move(1); }, 200);
    } catch (e) { if (msg) msg.className = "dw-dec-msg"; if (msg) msg.innerHTML = e.needName ? `<span class="bad">${WiderName.needHtml("")}</span>` : `<span class="bad">${h(e.message)}</span>`; }
    finally { deciding = false; }
  }

  function ignoredText() {
    return `${ignored} key press${ignored > 1 ? "es were" : " was"} ignored while the last record saved or loaded: nothing was decided for this record`;
  }

  function move(step) {
    clearTimeout(advanceTimer); advanceTimer = null;   // j/k during the pause moves once, not twice
    if (!cfg) return;
    const n = idx + step;
    if (n < 0 || n >= cfg.items.length) return;
    // the old item's controls are replaced (or Prev/Next turns disabled), so focus would drop to the page body:
    // while the next record loads the drawer itself holds it, then the new item's title takes it, as on open
    const a = document.activeElement;
    if (!a || a === document.body || q("#dw-body").contains(a) || q("#dw-notes").contains(a) || q("#dw-decide").contains(a)) el.focus({ preventScroll: true });
    idx = n; render().then(() => settleFocus(false));
  }

  function open(c) {
    if (!c.items || !c.items.length) return;
    // a queue that finished loading after the researcher left its page does not open over another page
    const tbl = c.table && q(c.table), page = tbl && tbl.closest && tbl.closest(".page");
    if (page && page.hidden) return;
    pendingFocus = null;
    clearTimeout(advanceTimer); advanceTimer = null;   // a reopened list does not jump on by the last decision's pause
    const box = c.dock && q(c.dock);
    if (c.dock && (!box || (box.closest && box.closest(".page") && box.closest(".page").hidden))) return;
    if (cfg && cfg.dock && !c.dock) undock();
    cfg = c; idx = Math.max(0, Math.min(c.index || 0, c.items.length - 1)); ignored = 0; lastSaved = null;
    lastFocus = document.activeElement;
    el.hidden = false;
    if (box) {
      if (el.parentNode !== box) box.appendChild(el);
      el.classList.add("docked"); dockedOn = location.hash;
    } else document.body.classList.add("drawer-open");
    q("#dw-close").hidden = !!box;
    // docked, the moves are worded buttons at the top right of the pair view; over the page, arrows
    q("#dw-prev").innerHTML = box ? "&#8249; Previous" : "&#8593;"; q("#dw-next").innerHTML = box ? "Next &#8250;" : "&#8595;";
    q(".dw-keys-esc").textContent = box ? "back to the list" : "close";
    // named by the record's title (D3); over the page a dialog that keeps Tab inside, docked a region beside its list
    el.setAttribute("role", box ? "region" : "dialog");   // aria-labelledby="dw-title" in index.html
    // over the page it is modal (inspection 1 Nov, item 17): aria-modal, and the page behind is inert
    if (box) { if (el.removeAttribute) el.removeAttribute("aria-modal"); } else el.setAttribute("aria-modal", "true");
    setBehind(!box);
    q(".dw-keys-notes").hidden = c.notes === false;  // no n/t keys where the drawer has no notes or tags
    q(".dw-keys-decide").hidden = !c.decisions;  // no 1/2/3 keys where the drawer is for reading only
    if (window.Guide && Guide.sync) Guide.sync();   // one side panel at a time: the side panel hides under the drawer
    // focus: false when the page drew the pair itself on loading: the focus stays where the researcher is
    render().then(() => { if (c.focus !== false) settleFocus(true); });
  }

  // leaving: true when the researcher moves to another page, which takes the focus itself
  function close(leaving) {
    if (!cfg) return;
    const c = cfg;
    const at = idx;
    cfg = null; pendingFocus = null; loading = false; shown = null; token++;  // a render still on its way is dropped
    clearTimeout(advanceTimer); advanceTimer = null;
    el.hidden = true; document.body.classList.remove("drawer-open"); setBehind(false);
    if (c.dock) undock();
    if (window.Guide && Guide.sync) Guide.sync();   // and comes back on its tab
    if (c.onClose) c.onClose();
    if (leaving === true) return;
    // back to the table row of the record last shown (tables re-render on close), else where the user came from;
    // c.backTo(item) finds that row when the re-rendered table lists other records at that place
    const t = c.table && q(c.table);
    const row = (c.backTo && c.backTo(c.items[at])) || t && (t.querySelector(`tr[data-idx="${at}"]`) || t.querySelector(`tr[data-idx="${at - 1}"]`) || t.querySelector("tr[data-idx]"));
    const back = row || (lastFocus && document.contains(lastFocus) ? lastFocus : null);
    if (back) back.focus({ preventScroll: false });
  }

  // the page regions behind the drawer over the page; the drawer is a child of body, never inside them
  function setBehind(on) {
    ["#topbar", "#sidebar", "#main", "#guide-panel"].forEach(sel => {
      const r = document.querySelector && document.querySelector(sel);
      if (!r || !r.setAttribute || !r.removeAttribute || (r.contains && r.contains(el))) return;
      if (on) r.setAttribute("inert", ""); else r.removeAttribute("inert");
    });
  }
  function undock() {
    if (home && el.parentNode !== home) home.appendChild(el);
    el.classList.remove("docked"); dockedOn = null;
    q("#dw-close").hidden = false;
    q("#dw-prev").innerHTML = "&#8593;"; q("#dw-next").innerHTML = "&#8595;";
  }

  q("#dw-prev").onclick = () => move(-1);
  q("#dw-next").onclick = () => move(1);
  q("#dw-close").onclick = () => close();

  document.addEventListener("keydown", (e) => {
    if (!cfg || el.hidden || e.metaKey || e.ctrlKey || e.altKey) return;
    const dlg = document.querySelector && document.querySelector("dialog[open]");
    if (dlg && dlg.open === true) return;   // a dialog over the drawer (the name dialog) keeps its own keys
    if (cfg.dock && location.hash !== dockedOn) return;   // a docked drawer's keys belong to its page
    const a = document.activeElement;
    const tag = (a && a.tagName) || "";
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag) || !!(a && a.isContentEditable);
    // a toast's Undo button sits outside the drawer: Tab reaches it, and the drawer's keys are not taken there
    const toasts = q("#toasts");
    const inToast = !!(a && a !== document.body && toasts && toasts.contains && toasts.contains(a));
    if (e.key === "Tab") {   // over the page, Tab and Shift+Tab wrap inside the drawer and its toasts; docked, the browser moves on
      if (cfg.dock) return;
      const sel = "a[href], button, input, select, textarea, summary, [tabindex]";
      const f = [...el.querySelectorAll(sel), ...(toasts ? toasts.querySelectorAll("a[href], button") : [])]
        .filter(x => !x.disabled && x.tabIndex >= 0 && x.getClientRects().length);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1], inside = el.contains(a) || inToast;
      if (e.shiftKey && (a === first || a === el || !inside)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (a === last || !inside)) { e.preventDefault(); first.focus(); }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      if (typing && el.contains(document.activeElement)) { document.activeElement.blur(); el.focus({ preventScroll: true }); }
      else if (cfg.dock) { if (cfg.onEsc) cfg.onEsc(); }   // docked: the drawer stays, the list takes the focus
      else close();
      return;
    }
    if (typing || inToast) return;
    // over the page the drawer is modal, so the page body counts as in it; docked, the focus must be in its box or list
    const box = cfg.dock && q(cfg.dock), list = cfg.table && q(cfg.table);
    const mine = cfg.dock ? !!a && a !== document.body && (el.contains(a) || (box && box.contains(a)) || (list && list.contains(a)))
      : !a || a === document.body || a === el || el.contains(a);
    if (!mine) return;
    const k = e.key;
    if (k === "j") { e.preventDefault(); move(1); }
    else if (k === "k") { e.preventDefault(); move(-1); }
    else if (/^[1-9]$/.test(k) && cfg.decisions && +k <= cfg.decisions.length) { e.preventDefault(); decide(+k - 1); }
    else if (k === "n" || k === "t") {
      const sel = k === "n" ? "#dw-note" : "#dw-tag";
      if (loading && cfg.notes !== false) { e.preventDefault(); pendingFocus = sel; return; }  // the field of the record on its way
      const t = q(sel); if (t) { e.preventDefault(); t.focus(); }
    }
  });

  // rows of any table opened in the drawer: click or Enter
  function bindRows(table, onOpen) {
    table.querySelectorAll("tr[data-idx]").forEach(tr => {
      tr.tabIndex = 0;
      tr.setAttribute("aria-describedby", "row-hint");   // "Press Enter to open it" (index.html), said once per row
      tr.onclick = (e) => { if (e.target.closest("button, a, input, select")) return; onOpen(+tr.dataset.idx); };
      tr.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); onOpen(+tr.dataset.idx); } };
    });
  }
  function markRow(table, i) {
    if (!table) return;
    table.querySelectorAll("tr[data-idx]").forEach(tr => tr.classList.toggle("active", +tr.dataset.idx === i));
    const tr = table.querySelector(`tr[data-idx="${i}"]`);
    if (tr) tr.scrollIntoView({ block: "nearest" });
  }

  // an undo puts the record back as it was: the same decision may be sent again
  if (window.addEventListener) window.addEventListener("wider:undone", () => { lastSaved = null; });

  window.Drawer = { open, close, move, highlight, bindRows, markRow, author, isOpen: () => !!cfg, isDeciding: () => deciding,
    isDocked: () => !!(cfg && cfg.dock), current: () => cfg && cfg.items[idx] };
})();
