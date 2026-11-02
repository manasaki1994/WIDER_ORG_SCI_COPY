// Background runs (WP14): a bar on every page with each running job's progress, ETA, cost so far and a Cancel
// button; a message (and a browser notification when the tab is in the background) when a run finishes; pages start
// long stages with Jobs.start(...) instead of waiting on the request.
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const h = esc;
  const call = api;
  const fmtDur = (s) => { if (s == null) return ""; s = Math.round(s); if (s < 60) return `${s} s`; const m = Math.floor(s / 60); if (m < 60) return `${m} min ${s % 60 ? (s % 60) + " s" : ""}`.trim(); return `${Math.floor(m / 60)} h ${m % 60} min`; };
  const LOC = { local: "this computer", cloud: "remote", network: "Local network", unknown: "unknown", test: "Test model (not for real reviews)" };

  window.usageText = function (u) {   // shared with the result cards (app.js) and the runs table
    if (!u || !u.calls) return "";
    const cost = u.cost_known ? `$${(u.cost_usd || 0).toFixed(u.cost_usd >= 1 ? 2 : 4)}` : (u.cost_usd ? `$${u.cost_usd.toFixed(4)} + ${u.unpriced_calls} unpriced calls` : "cost not priced");
    const cache = u.cached_tokens == null ? "cache use not reported" : `${u.cached_tokens.toLocaleString()} cached tokens`;
    const usd = x => `$${x.toFixed(x >= 1 ? 2 : 4)}`;   // as core/estimate.py:usd
    const proj = u.projected_min_usd != null ? ` · cost so far ${usd(u.cost_so_far_usd || 0)} · projected ${usd(u.projected_min_usd)}–${usd(u.projected_max_usd)}` : "";
    return `${(u.tokens || 0).toLocaleString()} tokens · ${cost} · ${cache}${proj}`;
  };
  window.usageChip = function (u) {
    if (!u || !u.locality) return "";
    const dest = (u.destinations || []).join(", ");
    const tip = u.locality === "local" ? "nothing left this computer" : u.locality === "test" ? "A stand-in model for practice. It gives no real answers."
      : dest ? `text was sent to ${dest}` : u.locality === "unknown" ? "where the text went was not established" : "text was sent off this computer";
    return `<span class="chip loc-${h(u.locality)}" title="${tip}">${h(LOC[u.locality] || u.locality)}</span>`;
  };

  let active = {}, timer = null, callbacks = {};
  const bar = q("#job-bar");

  function render(list) {
    if (!list.length) { bar.hidden = true; bar.innerHTML = ""; return; }
    bar.hidden = false;
    const canNotify = "Notification" in window && Notification.permission === "default";
    bar.innerHTML = list.map(j => {
      const pct = j.n_total ? Math.min(100, Math.round(100 * (j.n_done || 0) / j.n_total)) : null;
      const counts = j.n_total ? `${(j.n_done || 0).toLocaleString()} of ${j.n_total.toLocaleString()}` : "working";
      return `<div class="job" data-job="${h(j.job_id)}">
        <div class="job-line"><b>${h(j.label)}</b><span class="muted small">${counts}${j.eta_s != null ? ` · about ${fmtDur(j.eta_s)} left` : ""} · running ${fmtDur(j.elapsed_s)}</span>
          ${usageChip(j.usage)}<span class="muted small">${h(usageText(j.usage))}</span><span class="spacer"></span>
          <button class="secondary small-btn" data-cancel="${h(j.job_id)}">Cancel</button></div>
        <div class="job-track" role="progressbar" aria-label="${h(j.label)}" ${pct != null ? `aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"` : ""}><span style="width:${pct != null ? pct : 100}%" class="${pct == null ? "indeterminate" : ""}"></span></div>
        ${j.message ? `<div class="muted small">${h(j.message)}</div>` : ""}</div>`;
    }).join("") + (canNotify ? `<p class="small muted">You can leave this page; the run continues. <button class="secondary small-btn" id="job-notify">Notify me when runs finish</button></p>`
      : `<p class="small muted">You can leave this page; the run continues.</p>`);
    bar.querySelectorAll("[data-cancel]").forEach(b => b.onclick = async () => {
      if (!(await askConfirm("Cancel this run? Finished items are kept; starting the run again resumes it.", "Cancel the run"))) return;
      b.disabled = true;
      try { await call(`/api/jobs/${encodeURIComponent(b.dataset.cancel)}/cancel`, { method: "POST" }); } catch (e) { toast(e.message, "danger"); }
    });
    const n = q("#job-notify"); if (n) n.onclick = () => Notification.requestPermission().then(() => render(Object.values(active)));
  }

  // --toast-h: the stack's height, so a focused row scrolls clear of the toasts (rows' scroll-margin-bottom, D4)
  function toastHeight() {
    const box = q("#toasts"), root = document.documentElement;
    if (!box || !box.getBoundingClientRect || !root) return;
    root.style.setProperty("--toast-h", `${box.children.length ? Math.ceil(box.getBoundingClientRect().height) + 16 : 0}px`);
  }
  document.addEventListener("focusin", (e) => {
    const t = e.target;
    if (t && t.tagName === "TR" && t.scrollIntoView) t.scrollIntoView({ block: "nearest" });
  });

  // one short line read out per toast, in a live region that is always on the page (a toast box added later is not
  // read by every screen reader); a decision saved in the drawer says so there, so its Undo toast is not read twice
  function say(text, tone) {
    const live = q(tone === "danger" ? "#toast-alert" : "#toast-live");
    if (!live || (window.Drawer && Drawer.isDeciding && Drawer.isDeciding())) return;
    live.textContent = "";
    setTimeout(() => { live.textContent = String(text); }, 50);   // the same line twice is read twice
  }

  // a newer notice closes the earlier plain ones; errors stay until dismissed, action toasts (Undo) keep their timer
  let plain = [];
  function toast(text, tone, link, action) {
    const box = q("#toasts");
    plain.forEach(t => t.remove()); plain = [];
    const el = document.createElement("div");
    el.className = `toast tone-${tone || "success"}`;
    say(text, tone);
    el.innerHTML = `<span>${h(text)}</span>${link ? ` <a href="#${h(link)}">open</a>` : ""}${action ? ` <button class="secondary small-btn" data-act>${h(action.label)}</button>` : ""}<button class="chip-x" data-x aria-label="Dismiss">×</button>`;
    el.querySelector("[data-x]").onclick = () => el.remove();
    // the toast's button (Undo, Redo) gives the focus back to where the researcher was when the toast came (D2)
    const before = document.activeElement;
    if (action) el.querySelector("[data-act]").onclick = async () => {
      el.remove();
      if (window.focusAfter) focusAfter(before);   // first, so the action starts from there (an undo returns the focus to it)
      await action.fn();
      if (window.focusAfter) focusAfter(before);
    };
    const a = el.querySelector("a"); if (a) a.onclick = () => el.remove();
    const rm = el.remove.bind(el); el.remove = () => { rm(); toastHeight(); };   // every way a toast closes
    box.appendChild(el); toastHeight();
    // the 20 seconds wait while the toast holds the focus or the mouse (D4); failures stay until dismissed
    let due = false, over = false;
    const held = () => over || !!(el.contains && el.contains(document.activeElement));
    const close = () => { if (due && !held()) el.remove(); };
    if (el.addEventListener) {
      el.addEventListener("mouseenter", () => { over = true; });
      el.addEventListener("mouseleave", () => { over = false; close(); });
      el.addEventListener("focusout", () => setTimeout(close, 0));   // once the focus has landed elsewhere
    }
    if (tone !== "danger") setTimeout(() => { due = true; close(); }, 20000);
    if (tone !== "danger" && !action) plain.push(el);
    return el;
  }

  async function finished(job_id) {
    let j;
    try { j = await call(`/api/jobs/${encodeURIComponent(job_id)}`); } catch (e) { return; }
    const res = j.result && j.result.stage_result;
    if (j.status === "done") {
      const text = res ? res.headline : `${j.label}: finished.`;
      toast(text, res && res.tone === "warning" ? "warning" : "success", j.stage);
      if (res && window.showStageResult) showStageResult(res);
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        try { new Notification("WIDER: run finished", { body: text }); } catch (e) { /* not allowed here */ }
      }
    } else {
      const text = `${j.label}: ${j.status}${j.error ? ` (${j.error})` : ""}`;
      toast(text, j.status === "cancelled" ? "warning" : "danger", j.stage);
      if (j.status === "failed" && window.showStageError) showStageError(j.stage, j.error || "the run failed");
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        try { new Notification(`WIDER: run ${j.status}`, { body: text }); } catch (e) { /* not allowed here */ }
      }
    }
    if (callbacks[job_id]) { try { await callbacks[job_id](j); } catch (e) { /* page reload failed */ } delete callbacks[job_id]; }
    // the finished stage's page redraws; elsewhere only the sidebar (a page being typed in is left alone)
    if (typeof currentPage !== "undefined" && currentPage === j.stage && window.afterAction) afterAction();
    else if (window.refreshStages) refreshStages();
  }

  const stagesKey = () => Object.values(active).map(j => j.stage).filter(Boolean).sort().join(",");
  async function poll() {
    clearTimeout(timer);
    let list = [];
    try { list = await call("/api/jobs?active=true"); } catch (e) { list = Object.values(active); }
    const now = Object.fromEntries(list.map(j => [j.job_id, j]));
    for (const id of Object.keys(active)) if (!now[id]) finished(id);
    const before = stagesKey();
    active = now;
    render(list);
    // a stage whose job starts or ends changes its status word ("in progress") in the sidebar and the page chip
    if (stagesKey() !== before && window.renderShell && typeof STAGES !== "undefined" && STAGES.length) {
      renderShell(); if (window.renderStageWhy) renderStageWhy();
    }
    running = list.length; retitle();
    timer = setTimeout(poll, list.length ? 2000 : 15000);
  }

  // the page's own title (app.js showPage), with the number of runs in front while any goes on
  let running = 0;
  function retitle() { const t = window.pageTitle || "WIDER"; document.title = running ? `(${running} running) ${t}` : t; }

  window.Jobs = {
    retitle,
    // POST the start route; returns the job id, or the response when the server declined to start (e.g. weights missing)
    async start(path, opts = {}, onDone) {
      const r = await call(path, { method: "POST", ...opts });
      if (!r.job_id) return r;
      if (onDone) callbacks[r.job_id] = onDone;
      active[r.job_id] = { job_id: r.job_id, label: "starting…" };
      await poll();
      return r;
    },
    poll, toast,
    activeCount: () => Object.keys(active).length,
    runningStages: () => new Set(Object.values(active).map(j => j.stage).filter(Boolean)),
  };
  poll();
})();
