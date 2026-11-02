// The Screening review page (manual-review D1-D3, D11, D11a, D14): the records of one result set that need a person,
// each pass's read in a panel docked beside the list, decisions after screening, the set's criteria and
// quality-control samples. Twin of `wider review list|show|decide|criteria|qc draw`. It renders into named regions
// (#rv-toolbar, #rv-list, #rv-drawer, #rv-criteria) with `rv-` classes, so a redesign can move and restyle them.
(function () {
  const q = (s) => document.querySelector(s);
  const h = esc;
  const call = api;
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } } };
  const KEYS = { 1: "include", 2: "exclude", 3: "maybe" };   // the Labelling queue's keys
  const noName = () => `<span class="rv-noname">${WiderName.needHtml("no name yet: decisions need your name. ")}</span>`;   // one message, with the button (D33)
  const LOADING = "the next record is still loading: nothing was decided";
  let state = { protocol: null, set: null, show: "flagged", rows: [], index: -1, view: null, loading: false,
                last: null, fellBack: false };   // last: the decision Undo last takes back; fellBack: flagged was empty
  const NONE_FLAGGED = "No record needs a person, so every record of this set is listed";
  let seq = 0, openSeq = 0;

  const me = () => WiderName.get();
  const params = () => {
    const i = location.hash.indexOf("?");
    return i < 0 ? {} : Object.fromEntries(new URLSearchParams(location.hash.slice(i + 1)));
  };
  const qs = () => {
    const p = [];
    if (state.protocol) p.push("protocol=" + encodeURIComponent(state.protocol));
    if (state.set != null) p.push("set=" + encodeURIComponent(state.set));
    return p;
  };
  const url = (path, extra) => { const p = qs().concat(extra || []); return path + (p.length ? "?" + p.join("&") : ""); };
  const say = (t) => { q("#rv-msg").textContent = t || ""; };

  // ------------------------------------------------------------------ who signs
  function renderSigning() {
    const who = me();
    q("#rv-signing").innerHTML = who ? `Signing as <b>${h(who)}</b>` : noName();
  }

  // ------------------------------------------------------------------ the list
  async function load() {
    const n = ++seq, pr = params();
    if (pr.protocol) { state.protocol = pr.protocol; state.set = pr.set != null ? Number(pr.set) : null; }
    renderSigning();
    let d;
    try { d = await call(url("/api/review", ["show=" + encodeURIComponent(state.show)])); }
    catch (e) { if (n === seq) say(e.message); return; }
    if (n !== seq) return;   // a newer choice was made while this list loaded
    q("#rv-sentence").textContent = d.sentence;
    if (d.nothing) {
      q("#rv-empty").hidden = false;
      q("#rv-empty").innerHTML = `${h(d.nothing_line)}: run the <a href="#screen">Screening</a> step first.`;
      q("#rv-body").hidden = true; q("#rv-count").textContent = ""; state.rows = [];
      return;
    }
    q("#rv-empty").hidden = true; q("#rv-body").hidden = false;
    state.protocol = d.protocol; state.set = d.set;
    q("#rv-protocol").innerHTML = (d.protocols || []).map(p => `<option value="${h(p)}"${p === d.protocol ? " selected" : ""}>${h(p)}</option>`).join("");
    q("#rv-protocol").value = d.protocol;
    q("#rv-set").innerHTML = (d.sets || []).map(s => `<option value="${h(s.set)}"${s.set === d.set ? " selected" : ""}>${h(s.label)}${s.active ? " (active)" : ""}</option>`).join("");
    q("#rv-set").value = String(d.set);
    q("#rv-show").value = d.show;
    q("#rv-qc-sentence").hidden = !d.qc_drawn;
    q("#rv-qc-sentence").textContent = d.qc_sentence;
    q("#rv-count").textContent = state.fellBack && d.show === "all" ? `${NONE_FLAGGED}: ${d.count_line}.` : d.count_line;
    const hid = q("#rv-hidden");   // development records whose model decisions wait (ui-redesign D4)
    if (hid) { hid.hidden = !d.hidden; hid.textContent = d.hidden ? d.hidden + "." : ""; }
    state.rows = d.rows;
    renderList();
    loadCriteria();
    if (state.index >= state.rows.length) state.index = state.rows.length - 1;
  }
  function rowState(r) {
    if (r.failed) return "failed read";
    if (r.decided) return `decided${r.reviewer ? " by " + r.reviewer : ""}`;
    return r.needs_person ? "undecided" : "settled";
  }
  function renderList() {
    const rows = state.rows;
    q("#rv-list").innerHTML = rows.length ? `<table class="rec-table rv-table"><thead><tr><th>Record</th><th>Title</th><th>Why it is listed</th><th>Decision</th><th>State</th></tr></thead><tbody>`
      + rows.map((r, i) => `<tr data-rv-row="${i}" tabindex="0" aria-describedby="row-hint" class="rv-row${i === state.index ? " rv-current" : ""}"${i === state.index ? ' aria-current="true"' : ""}>
        <td>${h(r.record_id)}</td><td>${h(r.title)}</td><td class="rv-why">${h(r.why)}</td><td>${h(r.decision || "none")}</td><td>${h(rowState(r))}</td></tr>`).join("")
      + `</tbody></table>` : `<p>No record of this set is listed with this filter.</p>`;
    q("#rv-list").querySelectorAll("[data-rv-row]").forEach(tr => { tr.onclick = () => open(Number(tr.dataset.rvRow), true); });
  }

  // ------------------------------------------------------------------ the docked record
  function readHtml(x) {
    if (x.failed) return `<li data-rv-read="${h(x.pass)}"><b>${h(x.label)}</b>: failed</li>`;
    return `<li data-rv-read="${h(x.pass)}"><b>${h(x.label)}</b>: ${h(x.decision)}${x.model ? ` <span class="rv-model">(${h(x.model)})</span>` : ""}`
      + `${x.reason ? `: ${h(x.reason)}` : ""}${x.exclusion_reason ? `; exclusion reason: ${h(x.exclusion_reason)}` : ""}</li>`;
  }
  function renderDrawer(v) {
    const d = q("#rv-drawer");
    if (!v) { d.innerHTML = state.loading ? `<p class="rv-loading">loading</p>` : ""; return; }
    if (v.hidden) {   // a record of an incomplete development sample: no read, outcome or route, no decision
      d.innerHTML = `<p class="rv-recno">Record ${h(v.record_id)}</p><h2 class="rv-title">${h(v.title)}</h2>
        ${v.abstract ? `<p class="rv-abstract">${h(v.abstract)}</p>` : ""}<p class="rv-failed" role="note">${h(v.hidden)}.</p>`;
      return;
    }
    let act;
    if (v.failed) {
      act = `<p class="rv-failed">${h(v.failed_line)} <a href="#questions" data-rv-questions>open the Questions page</a></p>`;
    } else if (v.failed_answered) {
      act = `<p class="rv-failed">${h(v.failed_line)}</p>`;
    } else {
      // three big buttons with a coloured left edge, tinted when chosen (owner walkthrough; DESIGN.md A1)
      act = `<label class="rv-note">Note (optional) <input id="rv-note" type="text" size="40" autocomplete="off"></label>
        <div class="rv-decide dbtns" id="rv-decide" role="group" aria-label="Your decision">${["include", "exclude", "maybe"].map((k, i) => `<button type="button" class="dbtn" data-rv-decide="${k}" aria-pressed="${v.decided && v.final === k}"><kbd>${i + 1}</kbd> ${k[0].toUpperCase() + k.slice(1)}</button>`).join(" ")}</div>`;
    }
    const meta = [v.year, v.venue].filter(Boolean).map(h).join(" · ");
    d.innerHTML = `<p class="rv-recno">Record ${h(v.record_id)}${meta ? ` · ${meta}` : ""}</p><h2 class="rv-title">${h(v.title)}</h2>
      <p class="rv-why" id="rv-drawer-why">${h(v.why)}</p>
      ${v.abstract ? `<p class="rv-abstract">${h(v.abstract)}</p>` : ""}
      ${v.reads.length ? `<ul class="rv-reads">${v.reads.map(readHtml).join("")}</ul>` : ""}
      ${v.no_reads ? `<p class="rv-noreads">${h(v.no_reads)}</p>` : ""}
      <p class="rv-outcome">${v.llm_outcome != null ? `<span id="rv-llm">${h(v.outcome_label)}: ${h(v.llm_outcome)}</span> · ` : ""}<span id="rv-final">${h(v.final_line || "Final: none yet")}</span></p>
      ${act}
      <div class="undo-line rv-undo" aria-live="polite">${undoLine()}</div>
      <details class="rv-hist" id="rv-hist"><summary>History of this record</summary><div id="rv-hist-list"><p>loading</p></div></details>`;
    d.querySelectorAll("[data-rv-decide]").forEach(b => { b.onclick = () => decide(b.dataset.rvDecide); });
    d.querySelectorAll("[data-rv-undo]").forEach(b => { b.onclick = () => undoLast(); });
    loadHistory(v.record_id);
  }
  function undoLine() {
    const L = state.last;
    return L ? `<span>${h(L.text)}.</span> <button type="button" class="secondary small" data-rv-undo>Undo last</button>`
      : `<span>Each decision is saved as you choose it; Undo last appears here.</span>`;
  }
  async function undoLast() {
    const L = state.last;
    if (!L || !window.Undo) return;
    const r = await Undo.undo(L.id);   // History's undo: its message, the other-author dialog, then every page reloads
    if (r) state.last = null;
  }
  // the record's own decisions, newest first; hide_samples keeps sample names out (blinding)
  async function loadHistory(rid) {
    const n = openSeq;
    let rows;
    try { rows = await call(`/api/history?kind=review_decision&record_id=${encodeURIComponent(rid)}&hide_samples=true&limit=20`); }
    catch (e) { rows = null; }
    const box = q("#rv-hist-list");
    if (n !== openSeq || !box) return;
    if (!Array.isArray(rows)) { box.innerHTML = "<p>History could not be read.</p>"; return; }
    box.innerHTML = rows.length ? `<ul>${rows.map(c => `<li>${h(fmtWhen(c.at))}: ${h(c.summary)}${c.author ? ` (${h(c.author)})` : ""}${c.undone_by ? " (undone)" : ""}</li>`).join("")}</ul>`
      : "<p>No decision by a person yet: only the AI model's reads above.</p>";
  }
  async function open(i, focus) {
    if (i < 0 || i >= state.rows.length) return;
    state.index = i; state.view = null; state.loading = true;
    renderList(); renderDrawer(null);   // nothing of the earlier record stays readable while the next one loads
    const n = ++openSeq, rid = state.rows[i].record_id;
    let v;
    try { v = await call(url(`/api/review/${rid}`)); }
    catch (e) { if (n === openSeq) { state.loading = false; renderDrawer(null); say(e.message); } return; }
    if (n !== openSeq) return;
    state.view = v; state.loading = false;
    renderDrawer(v);
    if (focus) { const tr = q("#rv-list").querySelectorAll("[data-rv-row]")[i]; if (tr && tr.focus) tr.focus(); }
  }
  function nextUndecided(from) {
    for (let k = from + 1; k < state.rows.length; k++) if (state.rows[k].needs_person) return k;
    for (let k = 0; k <= from && k < state.rows.length; k++) if (state.rows[k].needs_person) return k;
    return -1;
  }
  // a key pressed while a decision is still being sent decides nothing, and says so with a count (never silently)
  let saving = false, ignored = 0;
  const refuse = (t) => { ignored++; say(`${t} (${ignored} key press${ignored > 1 ? "es" : ""} ignored)`); };
  async function decide(decision) {
    if (state.loading) { refuse(LOADING); return null; }
    if (saving) { refuse("the last decision is still being saved: nothing more was decided"); return null; }
    const v = state.view;
    if (!v) return null;
    saving = true; ignored = 0;
    try { return await send(decision, v); } finally { saving = false; }
  }
  async function send(decision, v) {
    renderSigning();   // the name may have changed on another page or tab since the list loaded
    if (!me()) { q("#rv-msg").innerHTML = noName(); return null; }
    if (v.failed || v.failed_answered) { say(v.failed_line); return null; }
    const note = q("#rv-note").value || "";
    let r;
    try {
      r = await call(url(`/api/review/${v.record_id}`), { method: "POST", headers: { "Content-Type": "application/json" },
                                                         body: JSON.stringify({ decision, reason: note }) });
    } catch (e) { say(e.message); return null; }
    if (!ignored) say("");   // a key refused while this saved keeps its line
    const at = state.index, rid = v.record_id;
    if (window.offerUndo) offerUndo(r.change_id, `Record ${rid}: ${r.final_line}`);
    state.last = { id: r.change_id, text: `Record ${rid}: ${r.final_line}` };
    await load();
    if (window.Review.onDecided) window.Review.onDecided();
    const here = state.rows.findIndex(x => x.record_id === rid);
    const nxt = nextUndecided(here >= 0 ? here : at - 1);
    if (nxt >= 0) await open(nxt, true); else { state.view = null; renderDrawer(null); renderList(); }
    return r;
  }

  // ------------------------------------------------------------------ keys: j/k move, 1/2/3 decide (not while typing)
  function typing(t) {
    if (!t) return false;
    const tag = String(t.tagName || "").toUpperCase();
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!t.isContentEditable;
  }
  async function onKey(e) {
    if (currentPage !== "review" || e.ctrlKey || e.metaKey || e.altKey) return;
    if (q("#rv-qc-dialog") && !q("#rv-qc-dialog").hidden) {   // the draw dialog: Esc closes it, Enter in a field draws
      const id = e.target && e.target.id;
      if (e.key === "Escape") { if (e.preventDefault) e.preventDefault(); closeQc(); }
      else if (e.key === "Enter" && (id === "rv-qc-n" || id === "rv-qc-seed")) { if (e.preventDefault) e.preventDefault(); await drawQc(); }
      return;
    }
    if (typing(e.target)) return;
    // single keys belong to the list and the docked record (WCAG 2.1.4): elsewhere on the page they do nothing
    const t = e.target, inside = (sel) => { const b = q(sel); return !!(t && b && b.contains && b.contains(t)); };
    if (!inside("#rv-list") && !inside("#rv-drawer")) return;
    if (e.key === "j" || e.key === "k") {
      if (e.preventDefault) e.preventDefault();
      const i = state.index < 0 ? 0 : state.index + (e.key === "j" ? 1 : -1);
      await open(Math.max(0, Math.min(state.rows.length - 1, i)), true);
    } else if (KEYS[e.key]) {
      if (e.preventDefault) e.preventDefault();
      await decide(KEYS[e.key]);
    } else if (e.key === "Escape" && state.index >= 0) {
      const tr = q("#rv-list").querySelectorAll("[data-rv-row]")[state.index]; if (tr && tr.focus) tr.focus();
    }
  }
  document.addEventListener("keydown", onKey);

  // ------------------------------------------------------------------ the criteria (folded, remembered per browser)
  async function loadCriteria() {
    const box = q("#rv-criteria");
    box.open = store.get("wider.review.criteria") === "open";
    let c;
    try { c = await call(url("/api/review/criteria")); } catch (e) { q("#rv-criteria-body").textContent = e.message; return; }
    q("#rv-criteria-body").innerHTML = `<p class="rv-crit-note">${h(c.note)}</p>` + c.sections.map(s => `<div class="rv-crit" data-rv-crit="${h(s.key)}"><h3>${h(s.title)}</h3>`
      + (s.text ? `<p>${h(s.text)}</p>` : `<ul>${s.items.map(x => `<li>${h(x)}</li>`).join("")}</ul>`) + `</div>`).join("");
  }
  document.addEventListener("widername", renderSigning);   // a first name set in the top bar or a dialog (D33)
  q("#rv-criteria").addEventListener("toggle", () => store.set("wider.review.criteria", q("#rv-criteria").open ? "open" : "closed"));

  // ------------------------------------------------------------------ quality-control sample (in-page dialog)
  function openQc() {
    q("#rv-qc-dialog").hidden = false; q("#rv-qc-msg").textContent = "";
    q("#rv-qc-signing").innerHTML = me() ? `Signing as <b>${h(me())}</b>` : noName();
    if (q("#rv-qc-n").focus) q("#rv-qc-n").focus();
  }
  function closeQc() { q("#rv-qc-dialog").hidden = true; if (q("#rv-qc-btn").focus) q("#rv-qc-btn").focus(); }
  async function drawQc() {
    if (!me()) { q("#rv-qc-msg").innerHTML = noName(); return null; }
    const n = q("#rv-qc-n").value, seed = q("#rv-qc-seed").value;
    const body = { n: n === "" ? null : Number(n) };
    if (seed !== "") body.seed = Number(seed);
    let d;
    try { d = await call(url("/api/review/qc"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
    catch (e) { q("#rv-qc-msg").textContent = e.message; return null; }
    closeQc();
    if (window.offerUndo) offerUndo(d.change_id, `Drew ${d.n} of ${d.pool_n} settled records (seed ${d.seed}).`);
    state.show = "qc"; state.index = -1;
    await load();
    return d;
  }
  q("#rv-qc-btn").onclick = openQc;
  q("#rv-qc-cancel").onclick = closeQc;
  q("#rv-qc-go").onclick = drawQc;

  // ------------------------------------------------------------------ toolbar
  q("#rv-protocol").onchange = () => { state.protocol = q("#rv-protocol").value; state.set = null; state.index = -1; clearAddress(); load(); };
  q("#rv-set").onchange = () => { state.set = Number(q("#rv-set").value); state.index = -1; clearAddress(); load(); };
  q("#rv-show").onchange = () => { state.show = q("#rv-show").value; state.fellBack = false; state.index = -1; load(); };
  // a chosen protocol or set replaces the address's, so a reload keeps the choice made on the page
  function clearAddress() {
    if (location.hash.includes("?") && window.history && history.replaceState) history.replaceState(null, "", "#review");
  }

  window.loadScreeningReview = async function () {
    await load();
    // nothing needs a person: list every record rather than an empty page, and say why (owner walkthrough)
    if (state.show === "flagged" && !state.rows.length && q("#rv-empty").hidden) {
      state.show = "all"; state.fellBack = true; await load();
    }
    if (state.rows.length && state.index < 0) {
      const first = nextUndecided(-1);
      await open(first >= 0 ? first : 0, false);
    } else if (state.index >= 0 && state.view) {
      await open(state.index, false);   // back on the page, or after an undo: the docked record is read again
    }
  };
  // an undo anywhere (toast, History, Undo last) may have taken back the decision the undo line names
  if (window.addEventListener) window.addEventListener("wider:undone", () => { state.last = null; });
  window.Review = { load, open, decide, onKey, drawQc, openQc, state: () => state, onDecided: null };
})();
