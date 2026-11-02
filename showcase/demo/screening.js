/* WIDER interactive demo: the screening runs, replayed (openspec change interactive-demo, design D5, task 2.4, 2.5).
 *
 * Loaded after actions.js. "Run" on the Screening page starts a simulated background job: the app's own job bar shows
 * its progress, and a list under the bar shows each record's recorded decisions (read A, read B and, where they differ,
 * the third read) as the run reaches it. When the run ends the demo moves to the next checkpoint, so every page shows
 * the recorded result. No model is called; the decisions are the ones the model gave when the demo was recorded.
 * The wording copies the app's own (wider/ui/jobs.py, wider/screen/sets.py, wider/ui/app.py).
 */
(function () {
  "use strict";
  const W = window, D = W.Demo, A = W.DemoActions;
  if (!D || !A) return;
  const { ok, refuse, plural, SIM } = A;
  const timing = { screen: 30000 };          // how long a simulated screening run takes, in ms (the tests set 0)
  const clock = { now: () => Date.now() };   // the tests replace it
  const SIM_MSG = "Simulated: the recorded decisions are replayed; no model is called and nothing costs anything.";
  const WORD = { include: "Include", exclude: "Exclude", maybe: "Maybe" };
  const KINDS = { screen: ["sampled", "screened"], screen2: ["screened", "screened2"] };   // [from, to] checkpoints
  const PASS = /__([A-Z])__[^_]+\.s(\d+)$/;

  const order = () => D.order();
  const idx = (name) => order().indexOf(name);
  const here = () => idx(D.state.checkpoint);
  const providers = (cp) => ((D.recorded("/api/providers", cp) || {}).providers || []);
  const firstProvider = () => (providers(KINDS.screen[0])[0] || {}).name;
  const secondProvider = () => {
    const had = new Set(providers(KINDS.screen[0]).map((p) => p.name));
    return (providers(KINDS.screen2[1]).find((p) => !had.has(p.name)) || {}).name;
  };
  const sets = (cp) => ((D.recorded("/api/screen/sets?protocol=main", cp) || {}).sets || []);
  const setOf = (kind) => sets(KINDS[kind][1]).find((s) => s.result_set === (kind === "screen" ? 1 : 2)) || null;
  const settingWords = (s) => {
    const b = ((s || {}).settings || {}).backend || {};
    return `${b.model || "the model"}, temperature ${b.temperature}`;
  };

  // ---- the recorded decisions, one row per record, in the order the run reaches them -------------------------------
  const rowCache = {};
  function rows(kind) {
    if (rowCache[kind]) return rowCache[kind];
    const s = setOf(kind), to = KINDS[kind][1], by = {};
    if (!s) return [];
    for (const r of s.runs || []) {
      const calls = D.recorded(`/api/screen/calls/${encodeURIComponent(r.run_id)}?limit=2000`, to) || [];
      for (const c of calls) {
        const row = by[c.record_id] = by[c.record_id] || { id: c.record_id, title: c.title };
        row[r.pass] = c.rule_decision || c.raw_decision || null;
      }
    }
    const out = Object.values(by).sort((a, b) => a.id - b.id);
    out.forEach((r) => { r.final = r.A === r.B ? r.A : (r.C || "maybe"); });
    if (kind === "screen2") {   // where the second round's decision differs from the first one's
      const first = {};
      rows("screen").forEach((r) => { first[r.id] = r.final; });
      out.forEach((r) => { if (first[r.id] && first[r.id] !== r.final) r.before = first[r.id]; });
    }
    return (rowCache[kind] = out);
  }

  // ---- the job: its clock, pause, cancel and end ---------------------------------------------------------------------
  const job = () => D.state.job || null;
  const done = () => (D.state.jobsDone = D.state.jobsDone || {});
  const spent = () => (D.state.jobsSpent = D.state.jobsSpent || {});   // kind -> ms run before a Cancel (a restart resumes)
  function elapsed(j) {
    const end = j.pausedAt || clock.now();
    return Math.max(0, end - j.started - (j.pausedFor || 0));
  }
  function progress(j) {
    const total = rows(j.kind).length || 1, t = timing.screen;
    const n = t <= 0 ? total : Math.min(total, Math.floor((elapsed(j) / t) * total));
    return { n, total, left: t <= 0 ? 0 : Math.max(0, t - elapsed(j)) };
  }
  function label(kind) {
    const s = setOf(kind);
    return `Screening: protocol ${(s && s.protocol) || "main"} on ${(s && s.scope) || "corpus"}`;
  }
  // a finished run: the demo moves on, and the result card shows the recorded result
  function settle() {
    const j = job();
    if (!j || progress(j).n < progress(j).total) return;
    const notes = A.filledIn().concat(j.notes || []);
    A.moveTo(j.to);
    const result = A.keep("screen", A.recordedResult("screen", j.to, notes));
    done()[j.id] = { job_id: j.id, status: "done", label: j.label, stage: "screen", result: { stage_result: result }, error: null };
    delete spent()[j.kind];
    D.state.job = null;
    D.save();
    say(j.kind);
  }
  function start(kind, notes) {
    const id = `demo-job-${(D.state.seq = (D.state.seq || 0) + 1)}`;
    D.state.job = { id, kind, from: KINDS[kind][0], to: KINDS[kind][1], label: label(kind), notes,
                    started: clock.now() - (spent()[kind] || 0), pausedAt: null, pausedFor: 0 };
    D.state.tickerHidden = false;
    settle();   // a run with no duration (the tests, or Skip) ends at once
    draw();
    return ok({ job_id: id });
  }
  function runNow(kind) {   // the tour's Skip: the run is done at once
    const j = job();
    if (j && j.kind === kind) { j.started = clock.now() - timing.screen - (j.pausedFor || 0) - 1; j.pausedAt = null; settle(); return true; }
    if (j || D.state.checkpoint !== KINDS[kind][0]) return false;
    spent()[kind] = timing.screen + 1;
    start(kind, []);
    return true;
  }
  const pause = () => { const j = job(); if (j && !j.pausedAt) { j.pausedAt = clock.now(); D.save(); draw(); } };
  const resume = () => {
    const j = job();
    if (j && j.pausedAt) { j.pausedFor = (j.pausedFor || 0) + (clock.now() - j.pausedAt); j.pausedAt = null; D.save(); draw(); }
  };

  // what the form asked for, when the demo's one recorded run is something else (inform, not control)
  function formNotes(b) {
    const asked = [];
    if (b.mode && b.mode !== "protocol") asked.push(b.mode === "pass" ? "mode “one pass”" : "mode “second screen of maybes”");
    if (b.scope && b.scope !== "corpus") asked.push(`records “${b.scope}”`);
    if (b.limit != null && String(b.limit).trim() !== "") asked.push(`a limit of ${b.limit}`);
    if (b.protocol && b.protocol !== "main") asked.push(`protocol name “${b.protocol}”`);
    if (!asked.length) return [];
    return [`The demo has one recorded run for this step: reads A and B on all ${rows("screen").length} records, then a ` +
      `third read where they differ (protocol “main”). Your choice of ${asked.join(", ")} was not used; in WIDER it would be.`];
  }

  D.handle("POST /api/jobs/screen", ({ body }) => {
    settle();
    const b = body || {}, prov = b.provider, first = firstProvider(), second = secondProvider();
    if (job()) return refuse(409, "A screening run is already running; its progress is in the bar at the top.");
    if (prov !== first && prov !== second) return refuse(409, `unknown backend '${prov}'`);
    // the earliest step still missing is named, as the app does
    if (here() < idx(order()[1])) return refuse(409, "Rank the records first (Ranking page).");
    if (here() < idx(KINDS.screen[0])) return refuse(409, "Draw the samples first (Samples page).");
    // ui-redesign D4: a run waits until every development record has a final label (the app's sentence and counts);
    // the tour's Skip labels them as Reviewer A did instead
    const open = D.state.checkpoint === KINDS.screen[0] ? A.devOpen() : 0;
    if (open) {
      return { status: 409, json: {
        detail: `the development sample has ${A.plural(open, "record")} without a final label; label them all first`,
        dev_incomplete: { samples: [{ sample: "development", missing: open, packages_out: [] }], missing: open } } };
    }
    const s1 = setOf("screen"), s2 = setOf("screen2");
    if (prov === first) {
      if (D.state.checkpoint === KINDS.screen[0]) return start("screen", formNotes(b));
      const run = ((s1 && s1.run_ids) || [])[0] || "";
      return ok({ already_done: true, message: `already done under model ${(s1 && s1.models || [])[0]}: run ${run} is finished; add another backend name for a separate run` + SIM });
    }
    // the second setting
    if (D.state.checkpoint === KINDS.screen[0]) {
      return refuse(409, `In the demo the first round is screened with the first setting (${settingWords(s1)}): choose ${first} and press Run. The second setting comes next.`);
    }
    if (D.state.checkpoint === KINDS.screen2[0]) {
      if (!b.new_set) {
        const changed = ["seed", "temperature"].filter((k) => {
          const a = ((s1 || {}).settings || {}).backend || {}, c = ((s2 || {}).settings || {}).backend || {};
          return a[k] !== c[k];
        });
        const n = (s1 && s1.n) || rows("screen").length;
        return { status: 409, json: {
          detail: `main set 1 is finished under ${((s1 && s1.models) || []).join(", ")}; your settings differ in: ${changed.join(", ")}. ` +
            "To screen again as a new result set, add --new-set" + SIM,
          new_set_needed: { protocol: "main", set: 2, changed, reusable: null, to_screen: n, in_scope: n, same_as: null, rule: "3", estimate: null },
        } };
      }
      const name = String(b.set_label || "").trim().slice(0, 80);
      if (name) A.overlay().setLabel = name;
      return start("screen2", formNotes(b));
    }
    const run = ((s2 && s2.run_ids) || [])[0] || "";
    return ok({ already_done: true, message: `already done under model ${(s2 && s2.models || [])[0]}: run ${run} is finished; add another backend name for a separate run` + SIM });
  });

  D.handle("GET /api/jobs", () => {
    settle();
    const j = job();
    if (!j) return ok([]);
    const p = progress(j);
    return ok([{ job_id: j.id, label: j.label, stage: "screen", n_done: p.n, n_total: p.total,
                 eta_s: j.pausedAt ? null : Math.round(p.left / 1000), elapsed_s: elapsed(j) / 1000, usage: {},
                 message: j.pausedAt ? "Paused with the tour: press Play in the tour panel to go on." : SIM_MSG }]);
  });

  D.handle("GET /api/jobs/{job_id}", ({ url }) => {
    settle();
    const id = decodeURIComponent(url.pathname.split("/")[3] || ""), j = job();
    if (j && j.id === id) return ok({ job_id: id, status: "running", label: j.label, stage: "screen", result: null, error: null });
    return done()[id] ? ok(done()[id]) : refuse(404, "no such job");
  });

  D.handle("POST /api/jobs/{job_id}/cancel", ({ url }) => {
    const id = decodeURIComponent(url.pathname.split("/")[3] || ""), j = job();
    if (!j || j.id !== id) return refuse(404, "no such job");
    spent()[j.kind] = elapsed(j);   // finished records are kept: Run again resumes
    done()[id] = { job_id: id, status: "cancelled", label: j.label, stage: "screen", result: null, error: null };
    D.state.job = null;
    draw();
    return ok({ ok: true });
  });

  D.handle("GET /api/screen/compare", () => ok({ comparisons: [] }));   // the card says "No comparison yet"

  D.handle("GET /api/screen/progress/{run_id}", ({ url }) => {
    const id = decodeURIComponent(url.pathname.split("/")[4] || ""), j = job();
    const m = PASS.exec(id);
    if (!j || !m) return undefined;   // a finished run: not recorded either, so "Not part of the demo"
    const s = setOf(j.kind);
    if (!s || !(s.run_ids || []).includes(id)) return undefined;
    const p = progress(j), seen = rows(j.kind).slice(0, p.n).filter((r) => r[m[1]]);
    const c = (d) => seen.filter((r) => r[m[1]] === d).length;
    return ok({ run_id: id, status: "running", n: seen.length, ok: seen.length, inc: c("include"), may: c("maybe"), exc: c("exclude") });
  });

  // the second round's name, as the visitor typed it (the recording has its own)
  D.patch("GET /api/screen/sets", (json) => {
    const name = A.overlay().setLabel;
    if (name && json && Array.isArray(json.sets)) json.sets.forEach((s) => { if (s.result_set === 2) s.label = name; });
    return json;
  });

  // ---- the Outputs page's two writes ---------------------------------------------------------------------------------
  D.handle("POST /api/outputs/prisma", () => {
    const r = D.recorded("/api/outputs/prisma", null, "POST");
    return r ? ok(r) : undefined;
  });
  D.handle("POST /api/outputs/methods", ({ body }) => {
    A.overlay().methods = String((body && body.text) || "").slice(0, 20000);
    return ok({ file: "methods.md" });
  });
  D.patch("GET /api/outputs/methods", (json) => {
    const t = A.overlay().methods;
    return t != null ? Object.assign({}, json, { file: "methods.md", text: t }) : json;
  });

  // ---- recall: the recorded estimate, with what a one-point interval means ----------------------------------------
  const cap = (x) => Math.round(x * 1000) / 1000;
  function pointNote(json) {
    const row = ((json.stage_result || {}).rows || []).find((r) => r.variant === json.primary_variant) || {};
    const ci = String(row["bootstrap 95% CI"] || "").split(/[–-]/).map(Number);
    if (ci.length !== 2 || ci[0] !== ci[1] || !row.relevant || row.missed) return null;
    const n = row.relevant, chance = cap(Math.pow(0.8, n));
    return `Demo note: the interval is a single point because the sample's ${plural(n, "relevant record")} were all kept. ` +
      `That is not certainty: with ${n} relevant records, a screen whose true recall is 80% would still keep all ${n} ` +
      `about ${Math.round(chance * 100)} times in 100. More labelled records give a more useful interval.`;
  }
  D.patch("GET /api/recall", (json) => {
    if (!json || !json.stage_result) return json;
    const note = pointNote(json), r = Object.assign({}, json.stage_result,
      { headline: json.stage_result.headline + SIM, ran_at: new Date().toISOString().slice(0, 19) + "+00:00" });
    if (note) r.warnings = (r.warnings || []).concat([note]);
    json.stage_result = A.keep("recall", r);
    return json;
  });
  // the recording also kept an estimate from an earlier WIDER version, whose bootstrap gave "95% CI 1.00–1.00" where
  // the page, the tour and the captions say 0.54 to 1.00: the list of earlier runs leaves it out (tester 1 Nov 2026)
  const OLD_METHOD = /bootstrap 95% CI/;
  D.patch("GET /api/stages/recall/results", (json) => (Array.isArray(json)
    ? json.filter((r) => !(r && r.action === "estimate" && OLD_METHOD.test(r.headline || ""))) : json));

  // ---- the list of decisions, under the Screening page's Run row (where the visitor pressed Run; the job bar is at
  // the top of the page, out of view) or, without that page, under the job bar ---------------------------------------
  const byId = (id) => document.getElementById(id);
  const short = (t) => (t && t.length > 70 ? t.slice(0, 68) + "…" : t || "");
  function line(r, kind) {
    const w = (d) => WORD[d] || d || "—";
    let s = `#${r.id} ${short(r.title)}: A ${w(r.A)} · B ${w(r.B)}`;
    if (r.A !== r.B) s += ` · third read ${w(r.C)}`;
    s += ` → ${w(r.final)}`;
    if (kind === "screen2" && r.before) s += ` (first round: ${w(r.before)})`;
    return s;
  }
  let parts = null;   // the list's elements, made once per page
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.setAttribute("class", cls);
    if (text) e.textContent = text;
    return e;
  }
  function box() {
    if (parts && byId("demo-ticker") === parts.root) return parts;
    const bar = byId("sc-msg") || byId("job-bar");
    if (!bar || !bar.parentNode) return null;
    const root = el("section", "demo-ticker"), head = el("div", "demo-ticker-head"), hide = el("button", "secondary small-btn", "Hide");
    root.setAttribute("id", "demo-ticker");
    root.setAttribute("aria-label", "Screening decisions, replayed");
    root.hidden = true;
    hide.setAttribute("type", "button");
    hide.addEventListener("click", () => { root.hidden = true; D.state.tickerHidden = true; D.save(); });
    parts = { root, count: el("b", "demo-ticker-count"), why: el("p", "demo-ticker-why muted small"), list: el("ul", "demo-ticker-list"),
              end: el("p", "demo-ticker-end") };
    parts.end.setAttribute("role", "status");
    parts.end.setAttribute("aria-live", "polite");
    head.appendChild(parts.count);
    head.appendChild(hide);
    [head, parts.why, parts.list, parts.end].forEach((x) => root.appendChild(x));
    bar.parentNode.insertBefore(root, bar.nextSibling);
    return parts;
  }
  function fill(list, kind) {
    parts.list.textContent = "";
    list.forEach((r) => {
      const li = el("li", r.A !== r.B ? "demo-ticker-split" : "", line(r, kind));
      parts.list.appendChild(li);
    });
  }
  let shown = -1;
  function draw() {
    const j = job(), b = box();
    if (!b) return;
    if (!j) { if (shown >= 0 && !b.end.textContent) b.root.hidden = true; shown = -1; return; }
    const p = progress(j);
    b.root.hidden = !!D.state.tickerHidden;
    b.count.textContent = `${p.n} of ${p.total} records decided${j.pausedAt ? " (paused)" : ""}`;
    b.why.textContent = "Each record is read twice (A and B). Where the two reads differ, a third read decides. " +
      "These are the model's recorded answers, replayed; the newest is on top.";
    b.end.textContent = "";
    if (p.n === shown) return;
    fill(rows(j.kind).slice(Math.max(0, p.n - 6), p.n).reverse(), j.kind);
    shown = p.n;
  }
  // once, when a run ends: one summary for screen readers, with the disagreements named
  function summary(kind) {
    const list = rows(kind), split = list.filter((r) => r.A !== r.B), c = (d) => list.filter((r) => r.final === d).length;
    let text = `Done: ${list.length} records decided${SIM}: ${c("include")} Include, ${c("maybe")} Maybe, ${c("exclude")} Exclude. ` +
      (split.length ? `${kind === "screen2" ? "In this round, r" : "R"}eads A and B differed on ${plural(split.length, "record")} (${split.map((r) => "#" + r.id).join(", ")}); ` +
        `the third read decided ${split.length === 1 ? "it" : "them"}.` : "Reads A and B agreed on every record.");
    if (kind === "screen2") {
      const moved = list.filter((r) => r.before);
      text += moved.length ? ` A different question: compared with the first round, the final decision changed for ${plural(moved.length, "record")}: ` +
        moved.map((r) => `#${r.id} ${WORD[r.before]} → ${WORD[r.final]}`).join(", ") + "." : " Every record ended as in the first round.";
    }
    return text;
  }
  function say(kind) {
    const b = box();
    if (!b) return;
    b.root.hidden = false;
    b.count.textContent = "The run is finished. Records where reads A and B differed:";
    b.why.textContent = "";
    fill(rows(kind).filter((r) => r.A !== r.B), kind);
    b.end.textContent = summary(kind);
    shown = -1;
  }
  setInterval(() => { settle(); draw(); }, 500);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", draw); else draw();

  // the demo's one recorded run is protocol A/B + third read on the whole corpus: the two lists beside "Run" start
  // there, once per visit (a choice the visitor makes afterwards stays theirs)
  function preselect() {
    const scope = byId("sc-scope"), mode = byId("sc-mode");
    if (!scope || !mode || typeof W.MutationObserver !== "function") return;
    const obs = new W.MutationObserver(() => {
      if (![...scope.options].some((o) => o.value === "corpus")) return;
      obs.disconnect();
      scope.value = "corpus";
      mode.value = "protocol";
      if (typeof mode.onchange === "function") mode.onchange(); else scope.dispatchEvent(new Event("change"));
    });
    obs.observe(scope, { childList: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", preselect); else preselect();

  // the history lists every read of a round as a line of its own (read A, read B, the third read), while the tour
  // speaks of rounds: say "runs" there, so "7 rounds" does not clash with the tour's two rounds
  function runsNotRounds() {
    const c = byId("sc-history-count");
    if (!c || typeof W.MutationObserver !== "function") return;
    const fix = () => {
      const m = /^\((\d+) rounds?\)$/.exec(c.textContent);
      if (m) c.textContent = `(${m[1]} runs: each read of a round, A, B and the third read, is listed on its own line)`;
    };
    fix();
    new W.MutationObserver(fix).observe(c, { childList: true, characterData: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", runsNotRounds); else runsNotRounds();

  W.DemoJobs = { timing, clock, pause, resume, stop: resume, runNow, rows, settle, summary, pointNote };
})();
