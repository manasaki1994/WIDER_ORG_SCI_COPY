// "Plan the validation sample" card and the "Recall target and rule" section on the Samples page (change
// validation-sample-planner). The card calls POST /api/validation/plan, which calls the engine `wider validation plan`
// calls (wider/sample/plan.py::run); the section calls POST /api/validation/target (`wider validation target`). The
// builders return HTML strings only (every value through `esc`, common.js); the controller is given its api, so tests
// run it under Node with fakes. Decisions and warnings are normal text, never grey.
const Validation = (() => {
  const num = (v) => { const s = String(v ?? "").trim(); return s === "" ? null : Number(s); };
  const txt = (v) => { const s = String(v ?? "").trim(); return s === "" ? null : s; };

  // the plan form as the route's body (the same fields as the CLI's options)
  function planBody(f) {
    return { budget: num(f.budget), precision: num(f.precision), rates: txt(f.rates), expected_recall: num(f.expected_recall),
             percentiles: txt(f.percentiles), allocation: txt(f.allocation) };
  }
  const fmt = (x) => x == null ? "" : Number(x).toFixed(3);
  function marginText(m) {
    return !m || m.median == null ? "no interval" : `${fmt(m.median)} (${fmt(m.p10)} to ${fmt(m.p90)})`;
  }
  // one row per allocation (middle rates) with its button, then its sensitivity rows (low and high rates) beneath
  function planTable(p) {
    if (!p || !p.rows) return "";
    const head = `<tr><th>Allocation</th><th>Labels per band</th><th>Relevant expected</th><th>Interval in</th><th>Margin (median, 10th to 90th)</th><th>Clear answer in</th><th></th></tr>`;
    const pct = (x) => x == null ? "" : `${Math.round(x * 100)}%`;
    const cells = (r) => `<td>${esc(Object.entries(r.allocation).map(([k, v]) => `${k} ${v}`).join(", "))}</td>`
      + `<td>${esc(Number(r.expected_relevant).toFixed(1))}</td><td>${esc(pct(r.p_interval))}</td>`
      + `<td>${esc(marginText(r.margin))}</td><td>${esc(pct(r.p_clear))}</td>`;
    const out = [];
    for (const r of p.rows.filter(r => r.rates === "middle")) {
      const rec = r.rule === p.recommended ? " <strong>recommended</strong>" : "";
      out.push(`<tr data-plan-row="${esc(r.rule)}"><td>${esc(r.rule)}${rec}</td>${cells(r)}`
        + `<td><button class="secondary" data-val-apply="${esc(r.rule)}">Use this allocation</button></td></tr>`);
      for (const extra of ["note", "line", "low_count_note"]) {
        if (r[extra]) out.push(`<tr><td></td><td colspan="6">${esc(r[extra])}</td></tr>`);
      }
      for (const s of p.rows.filter(s => s.rule === r.rule && s.rates !== "middle")) {
        out.push(`<tr data-plan-sens="${esc(r.rule)}"><td>if rates are ${esc(s.rates)}</td>${cells(s)}<td></td></tr>`);
      }
    }
    if (p.search && p.search.reached && !p.rows.some(r => r.rule === p.search.rule && r.rates === "middle")) {
      const s = p.search;
      out.push(`<tr data-plan-row="${esc(s.rule)}"><td>${esc(s.rule)} (smallest total)</td><td>${esc(Object.entries(s.allocation).map(([k, v]) => `${k} ${v}`).join(", "))}</td>`
        + `<td colspan="4">${esc(s.budget)} labels in all</td><td><button class="secondary" data-val-apply="${esc(s.rule)}">Use this allocation</button></td></tr>`);
    }
    const lines = (p.lines || []).map(l => `<p>${esc(l)}</p>`).join("");
    return `<table class="rec-table">${head}${out.join("")}</table>${lines}`;
  }

  // the target section as the route's body; depth only under top-strata (looks are not planned: the exact bounds hold
  // at every look, D15)
  function targetBody(f) {
    return { target: num(f.target), rule: txt(f.rule), depth: f.rule === "top-strata" ? num(f.depth) : null,
             precision: num(f.precision) };
  }

  function controller(parts, deps) {
    const st = { plan: null, form: {}, target: {} };
    const say = (m) => { if (parts.msg) parts.msg.textContent = m; };
    return {
      state: st,
      set(k, v) { st.form[k] = v; },
      setTarget(k, v) { st.target[k] = v; if (parts.depth) parts.depth.hidden = st.target.rule !== "top-strata"; },
      async plan() {
        say("Planning …");
        try { st.plan = await deps.api("/api/validation/plan", { method: "POST", body: JSON.stringify(planBody(st.form)) }); }
        catch (e) { st.plan = null; parts.table.innerHTML = ""; say(String(e.message || e)); return; }
        parts.table.innerHTML = planTable(st.plan); say("");
      },
      async use(rule) {
        const b = { ...planBody(st.form), apply: rule, author: deps.author ? deps.author() : null };
        try {
          const r = await deps.api("/api/validation/plan", { method: "POST", body: JSON.stringify(b) });
          say((r.applied && r.applied.lines || [`Bands set from ${rule}.`]).join(" "));
          if (deps.afterStep) await deps.afterStep();
        } catch (e) { say(String(e.message || e)); }
      },
      async saveTarget() {
        try {
          const r = await deps.api("/api/validation/target", { method: "POST", body: JSON.stringify(targetBody(st.target)) });
          if (parts.targetMsg) parts.targetMsg.textContent = r.status || r.output || "Saved.";
          if (deps.afterStep) await deps.afterStep();
        } catch (e) { if (parts.targetMsg) parts.targetMsg.textContent = String(e.message || e); }
      },
    };
  }
  // the Samples page's numbered steps (ui-redesign D32 F6): 1 recall target, 2 plan, 3 draw, 4 label. The first step
  // not done is the page's one filled button; the others are outline buttons. Nothing is disabled.
  function nextStep(s) {
    if (!s || s.target == null) return 1;
    if (!s.planned && !s.probability) return 2;
    if (!s.drawn) return 3;
    return 4;
  }
  function markSteps(buttons, step) {
    for (const b of buttons) b.className = Number(b.dataset.smStep) === step ? "main-act" : "secondary";
  }
  return { planBody, planTable, targetBody, controller, marginText, nextStep, markSteps };
})();
