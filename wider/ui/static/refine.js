// Query refinement on the Construct page (D2): the confirm steps that show what a save, freeze or unlock of the
// synonyms does to the frozen query and the validation papers, before anything is written. The notices come from
// GET /api/construct/synonyms (the same engine functions the CLI prints). Cancel writes nothing.
(function () {
  // a "Warning:" line (the syntax check at freeze time) is red, at normal size
  const lines = (text) => String(text || "").split("\n").filter(Boolean)
    .map(t => t.startsWith("Warning:") ? `<p class="warn-text">${esc(t)}</p>` : `<p>${esc(t)}</p>`).join("");

  async function notices() {
    try { return await api("/api/construct/synonyms"); } catch (e) { return null; }
  }

  // A pure builder: the freeze confirm. With `choice` the researcher picks one of two outcomes (neither pre-selected);
  // Cancel has the focus (askChoice).
  function freezeHtml(kind, version, notice, choice) {
    const acts = choice
      ? `<button type="button" data-act="count">Count them as seen</button>
         <button type="button" data-act="keep">Keep them as validation</button>`
      : `<button type="button" data-act="freeze">Freeze</button>`;
    return `<h2 id="choice-title">Freeze ${esc(kind)} v${esc(version)}?</h2>
      <p>Freeze ${esc(kind)} v${esc(version)}? It can no longer change status.</p>${lines(notice)}
      <div class="row">${acts}<button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }
  function saveHtml(title, notice, label) {
    return `<h2 id="choice-title">${esc(title)}</h2>${lines(notice)}
      <p class="muted small">This is recorded and final.</p>
      <div class="row"><button type="button" data-act="go">${esc(label)}</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }
  function unlockHtml(version, notice) {
    return `<h2 id="choice-title">Unlock synonyms v${esc(version)}?</h2>
      <p>v${esc(version)} is copied into a new draft to edit; v${esc(version)} stays frozen.</p>${lines(notice)}
      <p class="muted small">The unlock is recorded and final.</p>
      <div class="row"><button type="button" data-act="unlock">Unlock</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }

  // A pure builder: the confirm before a step that reopens final uploads while a frozen query stands and validation
  // papers were shown (D21, Q2): the two outcomes, neither pre-selected; Cancel has the focus and sends nothing.
  function seenHtml(title, notice) {
    return `<h2 id="choice-title">${esc(title)}</h2>${lines(notice)}
      <div class="row"><button type="button" data-act="count">Count them as seen</button>
      <button type="button" data-act="keep">Keep them as validation</button>
      <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
  }
  // Resolves "count", "keep" or null (Cancel, Escape).
  async function askSeen(title, notice) {
    const r = await askChoice(seenHtml(title, notice));
    return r && ["count", "keep"].includes(r.act) ? r.act : null;
  }
  // Runs `send(choice)` with no choice first; when the server answers 400 with the asking notice (nothing was done),
  // shows the confirm and sends again with the researcher's choice. Resolves null on Cancel.
  async function withSeenChoice(title, send) {
    try { return await send(null); }
    catch (e) {
      if (e.status !== 400 || !String(e.message).includes("The choice is final.")) throw e;
      const c = await askSeen(title, e.message);
      return c ? send(c) : null;
    }
  }
  // The same for a look at the hidden validation set (coverage, "Check the seed papers", the test without one part): the server
  // answers 200 with `choice_needed` and the notice (nothing shown or recorded). Resolves null on Cancel.
  async function lookWithSeenChoice(title, send) {
    const d = await send(null);
    if (!d || !d.choice_needed) return d;
    const c = await askSeen(title, d.notice);
    return c ? send(c) : null;
  }

  // Before a save of a synonyms version (save, restore, query import, propose): resolves true to go on, false on
  // Cancel. Other kinds, and a save that reopens nothing, go on without a step, unless `always` (a save the page
  // promises to ask about, such as Add to block) asks anyway. A save that reopens the query or the uploads after
  // validation papers were shown asks the seen choice and resolves "count" or "keep" (unlock-seen-choice D6).
  async function confirmSave(kind, title, label, always) {
    if (kind !== "synonyms") return true;
    const d = await notices();
    if (d && d.save_choice) return (await askSeen(title || "Save a new synonyms version?", d.save_notice)) || false;
    if (!always && (!d || !d.save_notice)) return true;
    const r = await askChoice(saveHtml(title || "Save a new synonyms version?", d && d.save_notice, label || "Save"));
    return !!(r && r.act === "go");
  }

  // The Freeze button: disabled on the frozen query (synonyms) or on any frozen version (other kinds).
  function freezeDisabled(kind, row, d) {
    if (kind !== "synonyms" || !d) return row.status === "frozen";
    return d.frozen_query === row.version || (row.status === "frozen" && !(row.version in (d.freeze_notice || {})));
  }

  async function afterLoad(kind, row) {
    const d = kind === "synonyms" ? await notices() : null;
    $("#art-freeze").disabled = freezeDisabled(kind, row, d);
    const un = $("#art-unlock");
    un.hidden = kind !== "synonyms";
    un.disabled = !(kind === "synonyms" && row.status === "frozen");
  }

  async function freeze(kind, version) {
    const d = kind === "synonyms" ? await notices() : null;
    const notice = d && d.freeze_notice ? d.freeze_notice[version] : null;
    const choice = !!(d && d.freeze_choice && d.freeze_choice[version]);
    const r = await askChoice(freezeHtml(kind, version, notice, choice));
    if (!r || !["freeze", "count", "keep"].includes(r.act)) return null;
    const q = r.act === "freeze" ? "" : `?seen_choice=${r.act}`;
    return api(`/api/construct/${kind}/${version}/status/frozen${q}`, { method: "POST" });
  }

  async function unlock(version) {
    const d = await notices();
    const notice = d && d.unlock_notice ? d.unlock_notice[version] : null;
    let seen_choice = null;
    if (d && d.unlock_choice && d.unlock_choice[version]) {
      seen_choice = await askSeen(`Unlock synonyms v${version}?`, notice);
      if (!seen_choice) return null;
    } else {
      const r = await askChoice(unlockHtml(version, notice));
      if (!r || r.act !== "unlock") return null;
    }
    const body = seen_choice ? { kind: "synonyms", version, seen_choice } : { kind: "synonyms", version };
    return api("/api/construct/unlock", { method: "POST", body: JSON.stringify(body) });
  }

  const status = (row) => {
    if (!(row && row.notice)) return;
    const el = $("#art-status");
    el.textContent = row.notice;
    el.className = row.notice.includes("Warning:") ? "warn-text" : "muted small";   // a warning is never grey
  };
  $("#art-freeze").onclick = async () => {
    if (typeof artCurrent === "undefined" || !artCurrent) return;
    try { const r = await freeze(artKind, artCurrent.version); if (r) { await loadConstruct(); status(r); } }
    catch (e) { $("#art-status").textContent = e.message; }
  };
  $("#art-unlock").onclick = async () => {
    if (typeof artCurrent === "undefined" || !artCurrent || artKind !== "synonyms") return;
    try { const r = await unlock(artCurrent.version); if (r) { await loadConstruct(); status(r); } }
    catch (e) { $("#art-status").textContent = e.message; }
  };

  window.Refine = { notices, freezeHtml, saveHtml, unlockHtml, seenHtml, askSeen, withSeenChoice, lookWithSeenChoice, confirmSave, freezeDisabled, afterLoad, freeze, unlock };
})();
