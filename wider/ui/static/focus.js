// Focus pages (label-focus-pages): the labelling queue and adjudication open the record drawer over the whole window,
// as in the owner's walkthrough L1 to L4. No sidebar, guide or journey line: a dark top
// bar (page name, who signs, previous / next, leave), the heading with a progress bar and the blinding note, the record
// card with the decisions under the abstract and the fields saved with them, and the criteria beside it. The drawer
// keeps its keys, saving, Tab trap and Esc; this file only moves its parts into the page and back on close.
// Focus.open(cfg) takes a Drawer.open cfg plus cfg.full = { title, signing (html), leave, progress(item) -> {text,
// done, total, more}, note (html), aside(item) -> html }. A body element .dw-after goes under the decisions, .fx-above
// above the record card; a decision's tone (include, exclude, maybe) colours its button.
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const h = esc;
  const el = q("#drawer");
  const EYE = '<svg class="fx-eye" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22"/></svg>';
  let active = null;   // the cfg shown full, until its drawer closes

  function progress() {
    if (!active) return;
    const f = active.full, item = Drawer.current();
    const p = (f.progress && item ? f.progress(item) : null) || {};
    q("#fx-progress").innerHTML = p.text ? h(p.text) : "";
    q("#fx-more").innerHTML = p.more ? h(p.more) : "";
    q("#fx-more").hidden = !p.more;
    const bar = q("#fx-bar");
    bar.hidden = !p.total;
    if (p.total) {
      bar.setAttribute("aria-valuemax", String(p.total)); bar.setAttribute("aria-valuenow", String(p.done || 0));
      q("i", bar).style.width = `${Math.round(100 * (p.done || 0) / p.total)}%`;
    }
  }
  // the decision buttons: their tone and which one is chosen (the drawer redraws them on every record)
  function tones(chosen) {
    if (!active || !active.decisions) return;
    q("#dw-decide").querySelectorAll("[data-dec]").forEach(b => {
      const d = active.decisions[+b.dataset.dec];
      if (d && d.tone) b.dataset.tone = d.tone;
      b.classList.add("fx-dec");
      b.setAttribute("aria-pressed", String(chosen != null && d && d.value === chosen));
    });
  }

  function build(c) {
    const f = c.full;
    el.classList.add("full"); document.body.classList.add("drawer-full");
    const head = q(".dw-head", el);
    head.insertAdjacentHTML("afterbegin", `<span class="fx-brand" id="fx-brand">WIDER</span><span class="fx-where" id="fx-where">${h(f.title)}</span>`);
    q("#dw-close").insertAdjacentHTML("beforebegin", `<span class="fx-sign" id="fx-sign">${f.signing || ""}</span><button type="button" class="fx-leave" id="fx-leave">${h(f.leave)} <kbd>Esc</kbd></button>`);
    q("#fx-leave").onclick = () => Drawer.close();
    head.insertAdjacentHTML("afterend", `<div class="fx-top" id="fx-top"><h1>${h(f.title)}</h1>
        <p class="fx-progress" id="fx-progress"></p>
        <div class="fx-bar" id="fx-bar" role="progressbar" aria-label="${h(f.barLabel || "Done")}" aria-valuemin="0" hidden><i></i></div>
        <p class="fx-more" id="fx-more" hidden></p>
        ${f.note ? `<p class="fx-note" id="fx-note">${EYE}<span>${f.note}</span></p>` : ""}</div>
      <div class="fx-grid" id="fx-grid"><div class="fx-main" id="fx-main"><div class="fx-above" id="fx-above"></div>
        <article class="fx-card" id="fx-card"></article></div><aside class="fx-crit" id="fx-crit" aria-label="The criteria"></aside></div>`);
    const card = q("#fx-card"), main = q("#fx-main");
    card.appendChild(q("#dw-body")); card.appendChild(q("#dw-decide"));
    card.insertAdjacentHTML("beforeend", '<div class="fx-after" id="fx-after"></div>');
    main.appendChild(q("#dw-notes")); main.appendChild(q(".dw-keys", el));
  }
  // the drawer's parts go back where index.html has them, so every other drawer is the side panel it was
  function unbuild() {
    const head = q(".dw-head", el);
    for (const s of ["#fx-brand", "#fx-where", "#fx-sign", "#fx-leave"]) { const x = q(s); if (x) x.remove(); }
    let at = head;
    for (const s of ["#dw-decide", "#dw-body", "#dw-notes", ".dw-keys"]) { const x = q(s, el); if (x) { at.after(x); at = x; } }
    for (const s of ["#fx-top", "#fx-grid"]) { const x = q(s); if (x) x.remove(); }
    el.classList.remove("full"); document.body.classList.remove("drawer-full");
    active = null;
  }

  function open(c) {
    if (active) unbuild();
    const f = c.full, cfg = { ...c };
    cfg.afterRender = (item, body) => {
      if (c.afterRender) c.afterRender(item, body);
      if (active !== cfg) return;
      const after = q("#fx-after"), above = q("#fx-above");
      after.innerHTML = ""; above.innerHTML = "";
      body.querySelectorAll(".dw-after").forEach(x => after.appendChild(x));
      body.querySelectorAll(".fx-above").forEach(x => above.appendChild(x));
      q("#fx-crit").innerHTML = f.aside ? f.aside(item) : "";
      q("#fx-crit").hidden = !q("#fx-crit").innerHTML;
    };
    cfg.onMove = (i, item) => {
      if (c.onMove) c.onMove(i, item);
      if (active !== cfg) return;
      tones(c.current ? c.current(item) : null); progress();
    };
    cfg.decisions = c.decisions && c.decisions.map(d => ({ ...d, fn: async (item, body) => {
      await d.fn(item, body);
      if (active === cfg && Drawer.current() === item) { tones(d.value); progress(); }
    } }));
    cfg.onClose = () => { if (active === cfg) unbuild(); if (c.onClose) c.onClose(); };
    active = cfg;
    build(c);
    Drawer.open(cfg);
    const want = c.items && c.items[Math.max(0, Math.min(c.index || 0, c.items.length - 1))];
    if (!Drawer.isOpen() || Drawer.current() !== want) unbuild();   // the queue's page was left meanwhile: nothing opens
  }

  window.Focus = { open, isOpen: () => !!active };
})();
