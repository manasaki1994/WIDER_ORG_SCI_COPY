// History and undo (WP15): the History page, "Undo" in the message after each manual change, Ctrl/Cmd+Z.
(function () {
  const q = (s) => document.querySelector(s);
  const h = esc;
  const call = api;
  const me = () => WiderName.get();   // the one name WIDER works as (D33)
  const toast = (t, tone, action) => window.Jobs && Jobs.toast(t, tone, null, action);
  // Blind (one switch with the Sampling page's box, ticked unless unticked there): History names no sample beside a
  // record, in the list and in the Undo and Redo messages (owner answer 37e6)
  const blindOn = () => { const lb = q("#lb-blind"), hb = q("#hist-blind"); return lb ? lb.checked : !hb || hb.checked; };
  const blindQ = () => blindOn() ? "hide_samples=true" : "";

  // after an undo every page may show stale data: close the drawer, reload the page and the stage marks; a drawer
  // docked in its page stays, and the page updates it in place (design-pass-a1-dedup D5)
  // resolves once the page is drawn again, so the caller can put the focus back (D2)
  async function refreshAll() {
    if (window.Drawer && Drawer.isOpen() && !(Drawer.isDocked && Drawer.isDocked())) Drawer.close();
    if (window.Questions) Questions.refresh();
    window.dispatchEvent(new CustomEvent("wider:undone"));
    if (window.afterAction) await afterAction();
  }

  // "undo my last change" needs to know who "my" is: without a name nothing is sent, and the message points at
  // Change in the top bar with a button that opens the name dialog
  const setName = { label: "Set your name", fn: () => WiderName.ask() };
  function needName(what) {
    if (typeof currentPage !== "undefined" && currentPage === "history" && q("#hist-msg")) q("#hist-msg").innerHTML = WiderName.needHtml("");
    toast(`${what} needs your name: set it with Change in the top bar. Nothing was ${what === "Undo" ? "undone" : "redone"}.`, "danger", setName);
  }


  // Another person's change (manual-review D7): the server undoes nothing and answers 409 with its author; the dialog
  // names them, and "Undo bo's change" sends the request again with other=true, signed with this page's name.
  function otherHtml(id, author) {
    const me_ = me();
    return `<h2 id="choice-title">Undo ${h(author)}'s change?</h2>
      <p>Change ${h(id)} was made by <b>${h(author)}</b>. The undo is signed ${me_ ? `<b>${h(me_)}</b>` : "with your name"}; History keeps both names.</p>
      <div class="row"><button type="button" data-act="other">Undo ${h(author)}'s change</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }
  async function withOther(id, send) {
    try { return await send(false); }
    catch (e) {
      const author = e.status === 409 && e.body && e.body.author;
      if (!author) throw e;
      return (await askChoice(otherHtml(id, author))) ? send(true) : null;
    }
  }

  let plans = {};   // change_id -> listed entry whose undo asks the seen choice (undo_choice, undo_notice)
  async function undo(id) {
    const back = document.activeElement;   // a queue row on Ctrl/Cmd+Z: it has the focus again after the redraw
    const who = id ? "" : me();
    if (q("#hist-near")) q("#hist-near").textContent = "";
    if (!id && !who) { needName("Undo"); return null; }
    try {
      // undoing the uploads-final mark under a frozen query with validation papers shown: the researcher chooses
      // first (D21, Q2); the listed entry carries the notice, and a 400 asking notice (nothing undone) is asked too
      const plan = id ? plans[id] : null;
      let r;
      const post = (c, o) => { const a = [c ? "seen_choice=" + c : "", o ? "other=true" : "", blindQ()].filter(Boolean).join("&"); return call(`/api/history/${id}/undo${a ? "?" + a : ""}`, { method: "POST" }); };
      if (plan && plan.undo_choice) {
        const c = await Refine.askSeen("Undo the uploads-final mark?", plan.undo_notice);
        if (!c) { q("#hist-msg").textContent = "Nothing was undone."; return null; }
        r = await withOther(id, (o) => post(c, o));
        if (!r) { q("#hist-msg").textContent = "Nothing was undone."; return null; }
      } else if (id) {
        r = await withOther(id, (o) => (window.Refine ? Refine.withSeenChoice : (t, send) => send(null))("Undo the uploads-final mark?", (c) => post(c, o)));
        if (!r) { q("#hist-msg").textContent = "Nothing was undone."; return null; }
      } else {
        // undoing the last change may undo the uploads-final mark: a 400 asking notice asks the seen choice (D6)
        const last = (c) => call(`/api/history/undo-last?author=${encodeURIComponent(who)}${c ? "&seen_choice=" + c : ""}${blindQ() ? "&" + blindQ() : ""}`, { method: "POST" });
        r = await (window.Refine ? Refine.withSeenChoice("Undo the last change?", last) : last(null));
        if (!r) { q("#hist-msg").textContent = "Nothing was undone."; return null; }
      }
      const redo = r.summary.startsWith("redo: ");
      offer(`${redo ? "Redone" : "Undone"}: ${r.summary.replace(/^(undo|redo): /, "").replace(/ \(change \d+\)$/, "")}${r.notice ? `. ${r.notice}` : ""}`, "success",
            { label: redo ? "Undo" : "Redo", fn: () => undo(r.change_id) });
      await refreshAll();
      if (window.focusAfter) focusAfter(back);
      return r;
    } catch (e) {
      // a name that differs only in case or spacing (D7): said once, in black on the History page, not as an error
      const near = q("#hist-near");
      if (!id && /; there are changes by '/.test(e.message) && near && near.offsetParent) { near.textContent = e.message; return null; }
      toast(e.message, "danger"); return null;   // a refusal is an error: read out as an alert
    }
  }
  // one Undo or Redo message at a time, so fast keyboard work does not pile them up and no older button acts on a
  // change already taken back
  let lastOffer = null;
  function offer(text, tone, action) {
    if (lastOffer) lastOffer.remove();
    lastOffer = toast(text, tone, action);
    return lastOffer;
  }
  // Ctrl/Cmd+Shift+Z (D4): put back this name's last undo; the CLI twin is `wider history redo`
  async function redo() {
    const back = document.activeElement, who = me();
    if (!who) { needName("Redo"); return null; }
    try {
      const r = await call(`/api/history/redo-last?author=${encodeURIComponent(who)}${blindQ() ? "&" + blindQ() : ""}`, { method: "POST" });
      offer(`Redone: ${r.summary.replace(/^redo: /, "").replace(/ \(change \d+\)$/, "")}${r.notice ? `. ${r.notice}` : ""}`, "success",
            { label: "Undo", fn: () => undo(r.change_id) });
      await refreshAll();
      if (window.focusAfter) focusAfter(back);
      return r;
    } catch (e) { toast(e.message, "danger"); return null; }   // nothing to redo, or a later change blocks it
  }
  window.offerUndo = function (changeId, text) {
    if (!changeId) return;
    offer(text || "Saved.", "success", { label: "Undo", fn: () => undo(changeId) });
  };
  window.Undo = { undo, redo, refreshAll };

  window.loadHistory = async function () {
    const sel = q("#hist-kind");
    if (!window.loadHistory.kinds) {   // the Show list comes from the engine, so every recorded kind can be chosen
      try {
        window.loadHistory.kinds = await call("/api/history/kinds");
        sel.innerHTML = `<option value="">all changes</option>` + window.loadHistory.kinds.map(k => `<option value="${h(k.kind)}">${h(k.label)}</option>`).join("");
      } catch (e) { /* the list keeps "all changes" */ }
    }
    const hb = q("#hist-blind");   // one Blind switch: the Sampling page's box and this one agree
    if (hb) hb.checked = blindOn();
    const kind = sel.value, mine = q("#hist-mine").checked, n = window.loadHistory.seq = (window.loadHistory.seq || 0) + 1;
    const blind = blindOn();   // Blind on: "in a sample" instead of the sample's name (owner answer 37e6)
    let rows;
    try { rows = await call(`/api/history?limit=200${kind ? "&kind=" + kind : ""}${mine ? "&author=" + encodeURIComponent(me()) : ""}${blind ? "&hide_samples=true" : ""}`); }
    catch (e) { if (n === window.loadHistory.seq) q("#hist-msg").textContent = e.message; return; }
    if (n !== window.loadHistory.seq) return;   // the filter changed again while this list loaded: the newer list is drawn
    plans = Object.fromEntries(rows.filter(c => c.undo_choice).map(c => [c.change_id, c]));
    q("#hist-msg").textContent = mine && !me() ? "set your name on the Sampling page (reviewer) to filter your changes" : `${rows.length} change${rows.length === 1 ? "" : "s"}`;
    q("#hist-table").innerHTML = rows.length ? `<thead><tr><th>when</th><th>who</th><th>what</th><th>change</th><th></th></tr></thead><tbody>` + rows.map(c =>
      `<tr class="${c.undone_by ? "undone" : ""}"><td class="small nowrap">${h(fmtWhen(c.at))}</td><td>${h((c.author || "") + (c.author_inferred ? " (inferred, not confirmed)" : ""))}</td><td class="small">${h(c.kind_label)}${c.record_id ? ` <span class="muted">· record ${c.record_id}</span>` : ""}</td>
        <td>${h(c.summary)}</td>
        <td class="${c.undone_by || c.undoable ? "nowrap" : ""}">${c.undone_by ? `<span class="muted small">undone</span>` : c.undoable ? `<button class="secondary small-btn" data-undo="${c.change_id}" aria-label="Undo change ${c.change_id}: ${h(c.summary)}">Undo</button>`
          : `<span class="muted small">cannot undo${c.why_not ? `: ${h(c.why_not)}` : ""}</span>`}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="muted">No changes recorded yet.</td></tr></tbody>`;
    q("#hist-table").querySelectorAll("[data-undo]").forEach(b => b.onclick = async () => {
      b.disabled = true;
      const r = await undo(+b.dataset.undo);
      if (!r) { b.disabled = false; return; }   // Cancel or a refusal: nothing changed, so the button works again
      if (window.focusAfter) focusAfter(null, `#hist-table [data-undo="${r.change_id}"]`);   // the new "undo:" entry's Undo, which redoes
    });
  };
  q("#hist-kind").onchange = loadHistory;
  q("#hist-mine").onchange = loadHistory;
  q("#hist-blind").onchange = () => {
    const lb = q("#lb-blind");
    if (lb) { lb.checked = q("#hist-blind").checked; lb.dispatchEvent(new Event("change")); }
    loadHistory();
  };
  q("#hist-undo-mine").onclick = () => undo(null);

  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey || e.key.toLowerCase() !== "z") return;
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || document.activeElement.isContentEditable) return;   // the field's own undo
    e.preventDefault();
    if (e.shiftKey) redo(); else undo(null);
  });

  // construct page: bring an older artifact version back as the newest draft
  q("#art-restore").onclick = async () => {
    const v = +q("#art-version").value;
    if (!v || typeof artKind === "undefined") return;
    try {
      const go = window.Refine ? await Refine.confirmSave(artKind, `Restore ${artKind} v${v} as a new version?`, "Restore") : true;
      if (!go) return;
      const r = await call(`/api/construct/${artKind}/${v}/restore${typeof go === "string" ? "?seen_choice=" + go : ""}`, { method: "POST" });
      toast(`${artKind} v${v} restored as v${r.version} (draft).` + (r.notice ? ` ${r.notice}` : ""), "success"); if (window.loadConstruct) loadConstruct();
    }
    catch (e) { toast(e.message, "danger"); }
  };
})();
